"""看图诊断 scope 库校验失败诊断：定位失败字段 + 拉取同级候选。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.graphs.img_diag_scope_display import SCOPE_FIELD_LABELS
from app.llm.graphs.img_diag_scope_intent import scope_dict_for_validate
from app.llm.graphs.img_diag_scope_validate import (
    bind_scope_validate_sql,
    validate_scope_in_catalog,
)
from app.nl2sql.scope_parser_rule import _has_explicit_row_no

logger = get_logger(__name__)

SCOPE_DIAGNOSE_FIELD_ORDER: tuple[str, ...] = (
    "boiler",
    "device_name",
    "check_location_name",
    "row_no",
    "tube_no",
)

# 仅机组存在性（validate SQL 要求锅炉+受热面，故单独提供）
_DEFAULT_DIAGNOSE_BOILER_SQL = """
SELECT COUNT(*) AS record_count
FROM account_boiler ab
WHERE ab.boiler_name = :boiler
""".strip()

_DEFAULT_CANDIDATE_BOILER_SQL = """
SELECT DISTINCT ab.boiler_name AS candidate_value
FROM account_boiler ab
WHERE ab.boiler_name IS NOT NULL AND TRIM(ab.boiler_name) <> ''
ORDER BY ab.boiler_name
LIMIT :limit
""".strip()

_DEFAULT_CANDIDATE_DEVICE_SQL = """
SELECT DISTINCT onc_surface.name AS candidate_value
FROM account_boiler ab
INNER JOIN overhaul_new_checklocation onc_surface
  ON onc_surface.boiler_id = ab.boiler_id
  AND IFNULL(onc_surface.del_flag, 0) = 0
WHERE ab.boiler_name = :boiler
  AND onc_surface.name IS NOT NULL AND TRIM(onc_surface.name) <> ''
ORDER BY onc_surface.name
LIMIT :limit
""".strip()

_DEFAULT_CANDIDATE_LOCATION_SQL = """
SELECT DISTINCT onc_loc.name AS candidate_value
FROM account_boiler ab
INNER JOIN overhaul_new_checklocation onc_surface
  ON onc_surface.boiler_id = ab.boiler_id
  AND IFNULL(onc_surface.del_flag, 0) = 0
  AND onc_surface.name LIKE CONCAT('%', :device_name, '%')
INNER JOIN overhaul_new_checklocation onc_loc
  ON onc_loc.parent_id = onc_surface.id
  AND IFNULL(onc_loc.del_flag, 0) = 0
WHERE ab.boiler_name = :boiler
  AND onc_loc.name IS NOT NULL AND TRIM(onc_loc.name) <> ''
ORDER BY onc_loc.name
LIMIT :limit
""".strip()

_DEFAULT_CANDIDATE_ROW_SQL = """
SELECT DISTINCT CAST(IFNULL(btp.row_num, 0) AS CHAR) AS candidate_value
FROM account_boiler ab
INNER JOIN overhaul_new_checklocation onc_surface
  ON onc_surface.boiler_id = ab.boiler_id
  AND IFNULL(onc_surface.del_flag, 0) = 0
  AND onc_surface.name LIKE CONCAT('%', :device_name, '%')
INNER JOIN base_temp_point btp
  ON btp.device_id = onc_surface.device_id
WHERE ab.boiler_name = :boiler
  AND IFNULL(btp.row_num, 0) > 0
ORDER BY IFNULL(btp.row_num, 0)
LIMIT :limit
""".strip()

_DEFAULT_CANDIDATE_TUBE_SQL = """
SELECT DISTINCT CAST(IFNULL(btp.pipe_num, 0) AS CHAR) AS candidate_value
FROM account_boiler ab
INNER JOIN overhaul_new_checklocation onc_surface
  ON onc_surface.boiler_id = ab.boiler_id
  AND IFNULL(onc_surface.del_flag, 0) = 0
  AND onc_surface.name LIKE CONCAT('%', :device_name, '%')
INNER JOIN base_temp_point btp
  ON btp.device_id = onc_surface.device_id
WHERE ab.boiler_name = :boiler
  AND IFNULL(btp.pipe_num, 0) > 0
  AND (
    :row_no IS NULL
    OR IFNULL(btp.row_num, 0) = :row_no
  )
ORDER BY IFNULL(btp.pipe_num, 0)
LIMIT :limit
""".strip()


@dataclass
class ScopeDiagnoseResult:
    failed_field: str | None
    matched_prefix: dict[str, Any] = field(default_factory=dict)
    user_value: Any = None
    injected_defaults: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed_field": self.failed_field,
            "failed_field_label": (
                SCOPE_FIELD_LABELS.get(self.failed_field or "", self.failed_field)
                if self.failed_field
                else None
            ),
            "matched_prefix": dict(self.matched_prefix),
            "user_value": self.user_value,
            "injected_defaults": list(self.injected_defaults),
        }


def _cfg():
    return get_app_config().analysis


def _sql_literal(value: str | None) -> str:
    if value is None or not str(value).strip():
        return "NULL"
    safe = str(value).replace("'", "''")
    return f"'{safe}'"


def _sql_int_literal(value: int | None) -> str:
    if value is None:
        return "NULL"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "NULL"
    return str(n) if n > 0 else "NULL"


def _bind_named_sql(sql_template: str, params: dict[str, Any]) -> str:
    sql = sql_template
    for key, val in params.items():
        token = f":{key}"
        if token not in sql:
            continue
        if key == "limit":
            try:
                n = max(1, int(val))
            except (TypeError, ValueError):
                n = 50
            sql = sql.replace(token, str(n))
        elif isinstance(val, int) or key in ("row_no", "tube_no"):
            sql = sql.replace(token, _sql_int_literal(val if isinstance(val, int) else None))
        else:
            sql = sql.replace(
                token,
                _sql_literal(str(val).strip() if val is not None and str(val).strip() else None),
            )
    return sql


async def _execute_count_sql(sql: str, *, executor: Any | None = None) -> int:
    if executor is None:
        from app.nl2sql.executor import SQLExecutor

        executor = SQLExecutor()
    timeout = float(getattr(_cfg(), "img_diag_scope_validate_timeout_s", 10.0))
    import asyncio

    rows = await asyncio.wait_for(executor.execute(sql), timeout=timeout)
    if not rows:
        return 0
    row = rows[0]
    count = row.get("record_count", row.get("COUNT(*)", 0))
    try:
        return int(count)
    except (TypeError, ValueError):
        return 0


async def _execute_candidate_sql(
    sql: str,
    *,
    executor: Any | None = None,
) -> list[str]:
    if executor is None:
        from app.nl2sql.executor import SQLExecutor

        executor = SQLExecutor()
    timeout = float(getattr(_cfg(), "img_diag_scope_validate_timeout_s", 10.0))
    import asyncio

    rows = await asyncio.wait_for(executor.execute(sql), timeout=timeout)
    out: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        raw = row.get("candidate_value")
        if raw is None:
            raw = next(iter(row.values()), None) if row else None
        text = str(raw).strip() if raw is not None else ""
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _prefix_scope_up_to(scope: dict[str, Any], field: str) -> dict[str, Any]:
    """构造校验到 field（含）为止的前缀 scope；更细字段置空。"""
    base = scope_dict_for_validate(scope)
    out: dict[str, Any] = {
        "boiler": None,
        "device_name": None,
        "check_location_name": None,
        "row_no": None,
        "tube_no": None,
    }
    for name in SCOPE_DIAGNOSE_FIELD_ORDER:
        out[name] = base.get(name)
        if name == field:
            break
    return out


async def _prefix_exists(
    scope_prefix: dict[str, Any],
    *,
    field: str,
    executor: Any | None = None,
) -> bool:
    """判断前缀是否在库中存在。锅炉单字段用独立 SQL；其余复用 validate（与 VALIDATE_SQL 口径一致）。"""
    if field == "boiler":
        boiler = (scope_prefix.get("boiler") or "").strip()
        if not boiler:
            return False
        tpl = (
            getattr(_cfg(), "img_diag_scope_diagnose_boiler_sql", None) or ""
        ).strip() or _DEFAULT_DIAGNOSE_BOILER_SQL
        # 若配置了完整 validate 风格 SQL，仍可用 bind；锅炉专用仅 :boiler
        if ":device_name" in tpl:
            sql = bind_scope_validate_sql(tpl, scope_prefix)
        else:
            sql = _bind_named_sql(tpl, {"boiler": boiler})
        try:
            return (await _execute_count_sql(sql, executor=executor)) > 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag scope diagnose boiler SQL failed: %s", exc)
            return False

    # 受热面及以上：走与线上一致的 validate SQL（可配置 ANALYSIS_IMG_DIAG_SCOPE_VALIDATE_SQL）
    if not scope_prefix.get("boiler") or not scope_prefix.get("device_name"):
        return False
    count, _err = await validate_scope_in_catalog(scope_prefix, executor=executor)
    return count > 0


def _prepare_scope_for_diagnose(
    scope: dict[str, Any],
    *,
    cumulative_text: str,
) -> tuple[dict[str, Any], list[str]]:
    """去掉非用户显式的默认 row_no=1，避免误诊为排数失败。"""
    current = scope_dict_for_validate(scope)
    injected: list[str] = []
    row = current.get("row_no")
    if (
        isinstance(row, int)
        and row == 1
        and not _has_explicit_row_no(cumulative_text or "")
    ):
        current = dict(current)
        current["row_no"] = None
        injected.append("row_no")
    return current, injected


async def diagnose_scope_db_failure(
    scope: dict[str, Any],
    *,
    cumulative_text: str = "",
    executor: Any | None = None,
) -> ScopeDiagnoseResult:
    """
    前缀探测：最长已命中前缀的下一层为 failed_field。
    与 validate SQL 口径一致（可选字段置 NULL）；机组层用 diagnose_boiler_sql。
    """
    current, injected = _prepare_scope_for_diagnose(scope, cumulative_text=cumulative_text)
    matched: dict[str, Any] = {}

    for field_name in SCOPE_DIAGNOSE_FIELD_ORDER:
        value = current.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        prefix = _prefix_scope_up_to(current, field_name)
        ok = await _prefix_exists(prefix, field=field_name, executor=executor)
        if ok:
            matched[field_name] = value
            continue
        return ScopeDiagnoseResult(
            failed_field=field_name,
            matched_prefix=matched,
            user_value=value,
            injected_defaults=injected,
        )

    # 全部前缀都过但仍合取失败（罕见，如校验 SQL 与诊断口径不一致）
    for field_name in reversed(SCOPE_DIAGNOSE_FIELD_ORDER):
        value = current.get(field_name)
        if value is not None and not (isinstance(value, str) and not str(value).strip()):
            return ScopeDiagnoseResult(
                failed_field=field_name,
                matched_prefix={
                    k: current.get(k)
                    for k in SCOPE_DIAGNOSE_FIELD_ORDER
                    if k != field_name and current.get(k) is not None
                },
                user_value=value,
                injected_defaults=injected,
            )
    return ScopeDiagnoseResult(
        failed_field="device_name",
        matched_prefix=matched,
        user_value=current.get("device_name"),
        injected_defaults=injected,
    )


def _candidate_sql_for_field(failed_field: str) -> str:
    cfg = _cfg()
    overrides = {
        "boiler": getattr(cfg, "img_diag_scope_candidate_sql_boiler", None),
        "device_name": getattr(cfg, "img_diag_scope_candidate_sql_device", None),
        "check_location_name": getattr(cfg, "img_diag_scope_candidate_sql_location", None),
        "row_no": getattr(cfg, "img_diag_scope_candidate_sql_row", None),
        "tube_no": getattr(cfg, "img_diag_scope_candidate_sql_tube", None),
    }
    defaults = {
        "boiler": _DEFAULT_CANDIDATE_BOILER_SQL,
        "device_name": _DEFAULT_CANDIDATE_DEVICE_SQL,
        "check_location_name": _DEFAULT_CANDIDATE_LOCATION_SQL,
        "row_no": _DEFAULT_CANDIDATE_ROW_SQL,
        "tube_no": _DEFAULT_CANDIDATE_TUBE_SQL,
    }
    custom = (overrides.get(failed_field) or "").strip() if failed_field in overrides else ""
    return custom or defaults.get(failed_field, "")


def candidate_limit() -> int:
    return max(1, int(getattr(_cfg(), "img_diag_scope_candidate_limit", 50) or 50))


async def fetch_scope_candidates(
    *,
    failed_field: str,
    matched_prefix: dict[str, Any],
    limit: int | None = None,
    executor: Any | None = None,
) -> list[dict[str, str]]:
    """按失败层 + 已命中上级拉取标准台账候选（截断）。"""
    tpl = _candidate_sql_for_field(failed_field)
    if not tpl:
        return []
    lim = max(1, int(limit if limit is not None else candidate_limit()))
    params: dict[str, Any] = {
        "boiler": matched_prefix.get("boiler"),
        "device_name": matched_prefix.get("device_name"),
        "check_location_name": matched_prefix.get("check_location_name"),
        "row_no": matched_prefix.get("row_no"),
        "tube_no": matched_prefix.get("tube_no"),
        "limit": lim,
    }
    sql = _bind_named_sql(tpl, params)
    try:
        values = await _execute_candidate_sql(sql, executor=executor)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "img_diag scope candidate SQL failed field=%s err=%s",
            failed_field,
            exc,
        )
        return []
    return [
        {"id": str(i + 1), "value": v, "label": v}
        for i, v in enumerate(values[:lim])
    ]
