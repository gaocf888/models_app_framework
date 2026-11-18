"""四元约束 + forced_tables 后调用 NL2SQLService.query。"""

from __future__ import annotations

import asyncio
import re
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


_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VIRTUAL_COL_KEYS = frozenset(
    {"station_id", "station_name", "device_name", "project_name", "area", "data_time"}
)
_ANNUAL_QUERY_RE = re.compile(
    r"年沉降|年度(?:变化|孔压|埋深|位移)|近一年|近\s*1\s*年|本年|去年"
)
_SNAPSHOT_TIME_TAGS = frozenset(
    {
        "",
        "latest_per_station",
        "yesterday_fallback",
        "today",
        "yesterday",
        "day_before_yesterday",
        "three_days_ago",
    }
)


def _time_tag(scope: ScopeIntentResult) -> str:
    tag = str((scope.time_snapshot or {}).get("time_window_tag") or "").strip()
    if tag:
        return tag
    return str((scope.annual_window or {}).get("tag") or "").strip()


def is_period_time(scope: ScopeIntentResult) -> bool:
    """区间时间窗（本月/本周/近N天等）为 True；今天/昨天/每站最新等快照口径为 False。"""
    tag = _time_tag(scope)
    if tag in _SNAPSHOT_TIME_TAGS or tag.startswith("day_"):
        return False
    return bool(tag)


def wants_annual_column(scope: ScopeIntentResult, query: str = "") -> bool:
    """仅当问句明确要年变化时才计算/展示年度列；「最新监测数据」不算。"""
    if is_period_time(scope):
        return False
    if _ANNUAL_QUERY_RE.search(query or ""):
        return True
    tag = _time_tag(scope)
    if tag == "latest_per_station" or tag in _SNAPSHOT_TIME_TAGS:
        return False
    return False


# 兼容旧私有名
_is_period_time = is_period_time


def _join_on(library: LibraryDef) -> str:
    from app.analysis_agent.deterministics.coverage import (
        uses_station_name_site_key,
        wash_t_station_join_sql,
    )

    device = (library.device_type or library.id or "").strip().lower()
    table = library.table
    if uses_station_name_site_key(device):
        return wash_t_station_join_sql(table, "t_station", device)
    return f"{table}.project_name = t_station.name"


def _site_filter_hint(library: LibraryDef) -> str:
    device = (library.device_type or library.id or "").strip().lower()
    if device == "gnss":
        return (
            "GNSS 禁止用 project_name 过滤站点（该列是工程总名「北京地面沉降监测」）；"
            "须用 station_name 等值匹配官方覆盖名单（与 yaml 站名一致，如 F15-GNSS、SLP01）。"
        )
    if device == "qxz":
        return (
            "气象站禁止用 project_name 过滤站点（该列是工程总名「北京地面沉降监测」）；"
            "须按 station_name 中 '-' 前的站码对齐覆盖名单括号前站码"
            "（F33-气象站 → F33，对应 yaml 的 F33(留士庄)）。"
        )
    return "站点范围用 project_name 对齐官方覆盖名单；JOIN 事实表.project_name = t_station.name。"


def _scope_block(scope: ScopeIntentResult) -> str:
    """注入 NL2SQL 问句的区/站/时间约束（未指定则由模板约定每站最新）。"""
    cs = scope.confirmed_scope
    district = cs.get("district") or "未指定"
    station = cs.get("station_id") or cs.get("station_name") or "未指定"
    tag = _time_tag(scope)
    tw = tag or "未指定则每站取最新 data_time"
    return f"【行政区】{district}；【站点】{station}；【时间】{tw}。列表按监测设备 station_name 展开，不要按场地 project_name 并成一行。"


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


def _physical_list_selects(library: LibraryDef) -> list[str]:
    """列表要查出的物理指标（含气象湿度/风、GNSS 位移等），避免只出 core_metric。"""
    skip = {
        (library.annual_key or "").strip(),
        (library.period_key or "").strip(),
        *_VIRTUAL_COL_KEYS,
    }
    skip.discard("")
    seen: set[str] = set()
    out: list[str] = []

    def add(physical: str, alias: str | None = None) -> None:
        p = (physical or "").strip()
        a = (alias or p).strip()
        if not p or p in skip or a in skip:
            return
        if not _IDENT_RE.match(p.lower()) or not _IDENT_RE.match(a.lower()):
            return
        if p in seen:
            return
        seen.add(p)
        out.append(f"{p} AS {a}" if a != p else p)

    core = (library.core_metric or "").strip()
    alias = (
        library.core_metrics[0].id
        if library.core_metrics
        else core
    )
    add(core, alias)
    for m in library.core_metrics:
        add(m.source or m.id, m.id)
    for col in library.columns:
        if col.key in skip:
            continue
        phys = ""
        for name in col.aliases or (col.key,):
            n = (name or "").strip()
            if n and n not in skip and _IDENT_RE.match(n.lower()):
                phys = n
                break
        add(phys or col.key, col.key)
    return out


def _metric_select(library: LibraryDef, scope: ScopeIntentResult, query: str = "") -> str:
    parts = _physical_list_selects(library)
    latest = ", ".join(parts) if parts else (library.core_metric or "")
    win = scope.annual_window or {}
    start = win.get("start") or ""
    end = win.get("end") or ""
    # 区间窗：周期差列用 period_key；仅明确「年沉降/年度变化」才要 annual_key
    if is_period_time(scope) and library.period_key and library.period_source:
        return (
            f"{latest}, "
            f"（窗 {start} 至 {end} 内 {library.period_source} 末日值减窗内最早值）"
            f"AS {library.period_key}"
        )
    if wants_annual_column(scope, query) and library.annual_key and library.annual_source:
        return (
            f"{latest}, "
            f"（窗 {start} 至 {end} 内 {library.annual_source} 末日值减窗内最早值）"
            f"AS {library.annual_key}"
        )
    return latest


def _series_select(library: LibraryDef, grain: str) -> str:
    cols = [m.id for m in (library.series_metrics or ()) if m.id]
    if not cols and library.series_column:
        cols = [library.series_column]
    if grain == "station":
        for frag in _physical_list_selects(library):
            name = frag.split(" AS ", 1)[0].strip()
            if name and name not in cols:
                cols.append(name)
    if grain in ("district", "city"):
        return ", ".join(f"AVG({c}) AS {c}" for c in cols)
    return ", ".join(cols)


def _uses_project_name_site(library: LibraryDef) -> bool:
    from app.analysis_agent.deterministics.coverage import uses_station_name_site_key

    device = (library.device_type or library.id or "").strip().lower()
    return not uses_station_name_site_key(device)


def _identity_select(library: LibraryDef) -> str:
    """列表身份列：场地用 project_name，设备用事实表 station_name。"""
    table = library.table
    if _uses_project_name_site(library):
        return (
            f"{table}.station_id AS station_id, "
            f"{table}.project_name AS project_name, "
            f"{table}.station_name AS device_name"
        )
    return (
        f"{table}.station_id AS station_id, "
        f"{table}.station_name AS station_name"
    )


def _identity_rule(library: LibraryDef) -> str:
    if _uses_project_name_site(library):
        return (
            "监测站名称必须用事实表 project_name（如 F8(周村)），"
            "监测设备必须用事实表 station_name AS device_name（如 K8-1 / F8-10）。"
            "禁止把 t_station.id 或场地名当成监测设备，禁止漏掉 device_name。"
        )
    return "监测站名称用事实表 station_name（如 F15-GNSS、F33-气象站）。"


def _is_fcb_library(library: LibraryDef) -> bool:
    return (library.device_type or library.id or "").strip().lower() == "fcb"


def _device_latest_rule(library: LibraryDef) -> str:
    """快照：一律按监测设备展开，禁止按场地并成一行。"""
    extra = ""
    if _is_fcb_library(library):
        extra = "分层标快照禁止用监测层位=0 的代表标过滤，必须输出该场地全部测点。"
    return (
        "必须按监测设备 station_name 一行一台：PostgreSQL 用 "
        "SELECT DISTINCT ON (station_name) … ORDER BY station_name, data_time DESC。"
        "禁止 DISTINCT ON (project_name) 把同一场地多台设备并成一行。"
        + extra
    )


def _rep_or_all_rule(library: LibraryDef) -> str:
    """周期变化：分层标仅代表标；其它监测方式全部设备。"""
    if _is_fcb_library(library):
        return (
            "分层标必须用监测层位=0 的代表标 station_name（禁止对同站全部标聚合）。"
            "一行一代表标。"
        )
    return (
        "这不是分层标：必须按监测设备 station_name 一行一台输出周期差，"
        "禁止用层位0/代表标过滤，禁止 DISTINCT ON (project_name) 或 PARTITION BY project_name "
        "把同一场地多台设备并成一行。一行一设备。"
    )


def _hud_device_rule(library: LibraryDef, grain: str) -> str:
    if grain != "station":
        return ""
    if _is_fcb_library(library):
        return (
            "快照 HUD 按监测设备 station_name 出各自时序，禁止只用层位 0 代表标一条曲线冒充全场地。"
        )
    return "HUD 按监测设备 station_name 出各自时序，禁止按 project_name 并场只留一台。"


def _device_grain_hint(library: LibraryDef, scope: ScopeIntentResult) -> str:
    if is_period_time(scope):
        return "【粒度】" + _rep_or_all_rule(library)
    return "【粒度】" + _device_latest_rule(library)


def _render_question(
    *,
    plan_item_id: str,
    grain: str,
    library: LibraryDef,
    scope: ScopeIntentResult,
    max_rows: int,
    query: str = "",
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
        if grain == "station" and _is_period_time(scope):
            tmpl = str(block.get("station_period") or block.get("station") or "")
        else:
            tmpl = str(block.get(grain) or block.get("station") or "")
    return tmpl.format_map(
        _FormatMap(
            table=library.table,
            display_name=library.display_name,
            library_id=library.id,
            metric_select=_metric_select(library, scope, query),
            core_column=library.core_metric,
            core_alias=(library.core_metrics[0].id if library.core_metrics else library.core_metric),
            period_alias=(library.period_key or "period_settle_mm"),
            series_column=library.series_column,
            series_select=_series_select(library, grain),
            scope_block=_scope_block(scope),
            annual_block=(
                _annual_block(library, scope)
                if wants_annual_column(scope, query)
                else ""
            ),
            join_on=_join_on(library),
            site_filter_hint=_site_filter_hint(library),
            max_rows=max_rows,
            identity_select=_identity_select(library),
            identity_rule=_identity_rule(library),
            device_latest_rule=_device_latest_rule(library),
            rep_or_all_rule=_rep_or_all_rule(library),
            hud_device_rule=_hud_device_rule(library, grain),
            device_grain_hint=_device_grain_hint(library, scope),
        )
    ).strip()


def _hint(library: LibraryDef, scope: ScopeIntentResult, query: str = "") -> str:
    plan = load_plan()
    tmpl = str(plan.get("hint_template") or "")
    cs = scope.confirmed_scope
    station = cs.get("station_id") or cs.get("station_name") or "未指定"
    tw = _time_tag(scope) or "未指定则每台设备取最新 data_time"
    return tmpl.format(
        display_name=library.display_name,
        library_id=library.id,
        table=library.table,
        district=cs.get("district") or "未指定",
        station=station,
        time_note=tw,
        annual_block=(
            _annual_block(library, scope) if wants_annual_column(scope, query) else ""
        ),
        join_on=_join_on(library),
        site_filter_hint=_site_filter_hint(library),
        device_grain_hint=_device_grain_hint(library, scope),
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
    # 站点 + 区间时间：优先确定性 period SQL；拼/执行失败则回退 LLM（不直接终态失败）。
    if plan_item_id == "q_list" and grain == "station" and _is_period_time(scope):
        try:
            from app.data_query_agent.period_sql import fetch_period_list

            sql, rows = await fetch_period_list(library, scope, max_rows=max_rows)
            logger.info(
                "data_query_agent period list deterministic library=%s rows=%s",
                library.id,
                len(rows),
            )
            return AcquireItemResult(plan_item_id=plan_item_id, ok=True, sql=sql, rows=rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "data_query_agent period_list_fallback_llm library=%s err=%s",
                library.id,
                exc,
                exc_info=True,
            )
            # fall through → 下方模板问句 + forced_tables LLM 路径
    question = _render_question(
        plan_item_id=plan_item_id,
        grain=grain,
        library=library,
        scope=scope,
        max_rows=max_rows,
        query=query,
    )
    hint = _hint(library, scope, query=query)
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
