"""四元约束 + forced_tables 后调用 NL2SQLService.query。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.data_query_agent.catalog import LibraryDef
from app.data_query_agent.scope_intent import ScopeIntentResult
from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse
from app.nl2sql.errors import NL2SQLExecutionError
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_plan_cache: dict[str, Any] | None = None


@dataclass
class AcquireItemResult:
    plan_item_id: str
    ok: bool
    sql: str = ""
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    gen_fail_reason: str | None = None


@dataclass
class AcquireResult:
    ok: bool
    list_item: AcquireItemResult
    series_item: AcquireItemResult | None = None
    error: str | None = None


class _FormatMap(dict):
    """模板缺 key 时填空串，避免 format 抛 KeyError。"""
    def __missing__(self, key: str) -> str:
        return ""


def load_plan() -> dict[str, Any]:
    global _plan_cache
    if _plan_cache is not None:
        return _plan_cache
    cfg = get_app_config().data_query_agent
    path = Path(cfg.plan_file)
    if not path.is_absolute():
        path = _REPO_ROOT / path
    _plan_cache = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _plan_cache


def clear_plan_cache() -> None:
    global _plan_cache
    _plan_cache = None


def _scope_block(scope: ScopeIntentResult) -> str:
    """注入 NL2SQL 问句的区/站/时间约束（未指定则由模板约定每站最新）。"""
    cs = scope.confirmed_scope
    district = cs.get("district") or "未指定"
    station = cs.get("station_id") or cs.get("station_name") or "未指定"
    tw = (scope.time_snapshot.get("time_window_tag") or "").strip() or "未指定则每站取最新 data_time"
    return f"【行政区】{district}；【站点】{station}；【时间】{tw}。"


def _annual_block(library: LibraryDef, scope: ScopeIntentResult) -> str:
    """年变化口径：窗内终值−初值；禁止 NULL 占位或串用其它监测表。"""
    if not library.annual_key or not library.annual_source:
        return ""
    win = scope.annual_window or {}
    start = win.get("start") or "近365天起点"
    end = win.get("end") or "当前"
    src = library.annual_source
    key = library.annual_key
    return (
        f"年度变化 {key} 必须计算为：data_time 落在 [{start}, {end}] 内该站 {src} 的"
        f"末日值减去窗内最早值（end_value - start_value），禁止用 NULL 占位，禁止使用其它监测表的列。"
    )


def _metric_select(library: LibraryDef, scope: ScopeIntentResult) -> str:
    core = library.core_metric
    alias = library.core_metrics[0].id if library.core_metrics else core
    latest = f"{core} AS {alias}" if alias != core else core
    if library.annual_key and library.annual_source:
        win = scope.annual_window or {}
        start = win.get("start") or ""
        end = win.get("end") or ""
        return (
            f"{latest}, "
            f"（窗 {start} 至 {end} 内 {library.annual_source} 末日值减窗内最早值）AS {library.annual_key}"
        )
    return latest


def _series_select(library: LibraryDef, grain: str) -> str:
    cols = [m.id for m in (library.series_metrics or ()) if m.id]
    if not cols and library.series_column:
        cols = [library.series_column]
    if grain in ("district", "city"):
        return ", ".join(f"AVG({c}) AS {c}" for c in cols)
    return ", ".join(cols)


def _render_question(
    *,
    plan_item_id: str,
    grain: str,
    library: LibraryDef,
    scope: ScopeIntentResult,
    max_rows: int,
) -> str:
    """按 grain 填 plan.yaml 模板；q_hud_series 站/区/市问句分叉。"""
    plan = load_plan()
    if plan_item_id == "q_hud_series":
        hud = plan.get("q_hud_series")
        if isinstance(hud, dict):
            tmpl = str(hud.get(grain) or hud.get("station") or "")
        else:
            tmpl = str(hud or "")
    else:
        block = plan.get("q_list") or {}
        tmpl = str(block.get(grain) or block.get("station") or "")
    return tmpl.format_map(
        _FormatMap(
            table=library.table,
            display_name=library.display_name,
            library_id=library.id,
            metric_select=_metric_select(library, scope),
            core_column=library.core_metric,
            core_alias=(library.core_metrics[0].id if library.core_metrics else library.core_metric),
            series_column=library.series_column,
            series_select=_series_select(library, grain),
            scope_block=_scope_block(scope),
            annual_block=_annual_block(library, scope),
            max_rows=max_rows,
        )
    ).strip()


def _hint(library: LibraryDef, scope: ScopeIntentResult) -> str:
    plan = load_plan()
    tmpl = str(plan.get("hint_template") or "")
    cs = scope.confirmed_scope
    station = cs.get("station_id") or cs.get("station_name") or "未指定"
    tw = (scope.time_snapshot.get("time_window_tag") or "").strip() or "未指定则每站取最新 data_time"
    return tmpl.format(
        display_name=library.display_name,
        library_id=library.id,
        table=library.table,
        district=cs.get("district") or "未指定",
        station=station,
        time_note=tw,
        annual_block=_annual_block(library, scope),
    ).strip()


def _sql_table_ok(sql: str, table: str) -> bool:
    """生成 SQL 必须含本库表，且不得再出现其它 t_data_wash_*。"""
    low = (sql or "").lower()
    if table.lower() not in low:
        return False
    import re

    others = re.findall(r"t_data_wash_[a-z0-9_]+", low)
    return all(t == table.lower() for t in others)


def _filters(scope: ScopeIntentResult) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    cs = scope.confirmed_scope
    if cs.get("district"):
        out["district"] = cs["district"]
        out["area"] = cs["district"]
    if cs.get("station_id"):
        out["station_id"] = cs["station_id"]
    if cs.get("station_name"):
        out["station_name"] = cs["station_name"]
    return out or None


async def _query_one(
    *,
    nl2sql: NL2SQLService,
    user_id: str,
    session_id: str,
    request_id: str,
    query: str,
    library: LibraryDef,
    scope: ScopeIntentResult,
    plan_item_id: str,
    grain: str,
    max_rows: int,
) -> AcquireItemResult:
    """单路 NL2SQL：analysis_type=data_query，链错表 refuse，SQL 再校验 wash 表。"""
    question = _render_question(
        plan_item_id=plan_item_id,
        grain=grain,
        library=library,
        scope=scope,
        max_rows=max_rows,
    )
    hint = _hint(library, scope)
    req = NL2SQLQueryRequest(
        user_id=user_id,
        session_id=session_id,
        question=question,
        analysis_type="data_query",
        analysis_request_id=request_id,
        plan_item_id=plan_item_id,
        time_intent_text=query,
        original_query=query,
        confirmed_scope=dict(scope.confirmed_scope),
        scope_intent_text=hint,
        sql_gen_extra_hint=hint,
        structured_filters=_filters(scope),
        disable_qa_slot_replay=True,
        on_link_failure="refuse",
        # 查询台硬锁主表；不传 forced_tables 的其它调用方不受影响。
        forced_tables=[library.table],
    )
    try:
        resp: NL2SQLQueryResponse = await nl2sql.query(
            req,
            record_conversation=False,
            include_parsed_intent=True,
        )
    except NL2SQLExecutionError as exc:
        logger.warning(
            "data_query_agent acquire exec failed plan_item=%s err=%s",
            plan_item_id,
            exc,
        )
        return AcquireItemResult(
            plan_item_id=plan_item_id,
            ok=False,
            sql=getattr(exc, "sql", "") or "",
            error=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("data_query_agent acquire failed plan_item=%s", plan_item_id)
        return AcquireItemResult(plan_item_id=plan_item_id, ok=False, error=str(exc))

    sql = resp.sql or ""
    reason = resp.gen_fail_reason
    if not sql.strip() or reason:
        return AcquireItemResult(
            plan_item_id=plan_item_id,
            ok=False,
            sql=sql,
            gen_fail_reason=reason,
            error=reason or "empty_sql",
        )
    if not _sql_table_ok(sql, library.table):
        return AcquireItemResult(
            plan_item_id=plan_item_id,
            ok=False,
            sql=sql,
            error="sql_table_mismatch",
        )
    rows = [r for r in (resp.rows or []) if isinstance(r, dict)]
    return AcquireItemResult(plan_item_id=plan_item_id, ok=True, sql=sql, rows=rows)


async def acquire_data(
    *,
    nl2sql: NL2SQLService,
    user_id: str,
    session_id: str,
    request_id: str,
    query: str,
    library: LibraryDef,
    scope: ScopeIntentResult,
    include_hud: bool,
    max_rows: int,
    cancelled_check=None,
) -> AcquireResult:
    """q_list 必跑；HUD 时再并行 q_hud_series（同一库表与区/站/时间）。"""
    if library is None:
        return AcquireResult(
            ok=False,
            list_item=AcquireItemResult(plan_item_id="q_list", ok=False, error="library_not_locked"),
            error="library_not_locked",
        )
    cs = scope.confirmed_scope or {}
    tw = scope.time_snapshot or {}
    logger.info(
        "data_query_agent acquire start request_id=%s library=%s table=%s "
        "district=%s station_id=%s station_name=%s time_tag=%s include_hud=%s on_link_failure=refuse",
        request_id,
        library.id,
        library.table,
        cs.get("district") or "",
        cs.get("station_id") or "",
        cs.get("station_name") or "",
        tw.get("time_window_tag") or "",
        include_hud,
    )
    cfg = get_app_config().data_query_agent
    sem = asyncio.Semaphore(max(1, int(cfg.acquire_max_parallel)))

    async def _guarded(coro):
        async with sem:
            if cancelled_check is not None and await cancelled_check():
                return AcquireItemResult(plan_item_id="cancelled", ok=False, error="cancelled")
            return await coro

    list_coro = _query_one(
        nl2sql=nl2sql,
        user_id=user_id,
        session_id=session_id,
        request_id=request_id,
        query=query,
        library=library,
        scope=scope,
        plan_item_id="q_list",
        grain=scope.grain,
        max_rows=max_rows,
    )
    city_ok = bool(getattr(cfg, "hud_city_enabled", True))
    # include_hud=false 或 grain 不支持时只跑列表，避免无实体仍拉时序。
    hud_grains = {"station", "station_series", "district"}
    if city_ok:
        hud_grains.add("city")
    want_hud = include_hud and library.hud_supported and scope.grain in hud_grains
    if want_hud:
        series_coro = _query_one(
            nl2sql=nl2sql,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            query=query,
            library=library,
            scope=scope,
            plan_item_id="q_hud_series",
            grain=scope.grain,
            max_rows=max_rows,
        )
        list_item, series_item = await asyncio.gather(
            _guarded(list_coro),
            _guarded(series_coro),
        )
    else:
        list_item = await _guarded(list_coro)
        series_item = None

    if getattr(list_item, "error", None) == "cancelled" or (
        series_item is not None and series_item.error == "cancelled"
    ):
        return AcquireResult(ok=False, list_item=list_item, series_item=series_item, error="cancelled")
    if not list_item.ok:
        return AcquireResult(
            ok=False,
            list_item=list_item,
            series_item=series_item,
            error=list_item.error or "q_list_failed",
        )
    return AcquireResult(ok=True, list_item=list_item, series_item=series_item)
