"""看图诊断 scope 库表 existence 校验（第二层）与分级放宽。"""

from __future__ import annotations

from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.graphs.img_diag_scope_intent import relax_scope_one_level, scope_dict_for_validate

logger = get_logger(__name__)

_DEFAULT_VALIDATE_SQL = """
SELECT COUNT(*) AS record_count
FROM account_boiler ab
INNER JOIN account_static_device asd ON ab.boiler_id = asd.boiler_id
LEFT JOIN overhaul_new_checklocation onc ON asd.device_id = onc.device_id
  AND IFNULL(onc.del_flag, 0) = 0
WHERE ab.boiler_name = :boiler
  AND asd.device_name = :device_name
  AND (
    :check_location_name IS NULL
    OR onc.name LIKE CONCAT('%', :check_location_name, '%')
  )
  AND (
    :row_no IS NULL
    OR EXISTS (
      SELECT 1 FROM base_temp_point btp
      WHERE btp.device_id = asd.device_id
        AND IFNULL(btp.row_num, 0) = :row_no
        AND (:tube_no IS NULL OR IFNULL(btp.pipe_num, 0) = :tube_no)
    )
  )
  AND (
    :tube_no IS NULL
    OR :row_no IS NOT NULL
    OR EXISTS (
      SELECT 1 FROM base_temp_point btp2
      WHERE btp2.device_id = asd.device_id
        AND IFNULL(btp2.pipe_num, 0) = :tube_no
    )
  )
""".strip()

DEFAULT_IMG_DIAG_SCOPE_VALIDATE_SQL = (
    "SELECT COUNT(*) AS record_count FROM account_boiler ab "
    "INNER JOIN account_static_device asd ON ab.boiler_id = asd.boiler_id "
    "LEFT JOIN overhaul_new_checklocation onc ON asd.device_id = onc.device_id "
    "AND IFNULL(onc.del_flag, 0) = 0 "
    "WHERE ab.boiler_name = :boiler AND asd.device_name = :device_name "
    "AND (:check_location_name IS NULL OR onc.name LIKE CONCAT('%', :check_location_name, '%')) "
    "AND (:row_no IS NULL OR EXISTS ("
    "SELECT 1 FROM base_temp_point btp WHERE btp.device_id = asd.device_id "
    "AND IFNULL(btp.row_num, 0) = :row_no "
    "AND (:tube_no IS NULL OR IFNULL(btp.pipe_num, 0) = :tube_no))) "
    "AND (:tube_no IS NULL OR :row_no IS NOT NULL OR EXISTS ("
    "SELECT 1 FROM base_temp_point btp2 WHERE btp2.device_id = asd.device_id "
    "AND IFNULL(btp2.pipe_num, 0) = :tube_no))"
)


def default_scope_validate_sql() -> str:
    cfg = get_app_config().analysis
    custom = (getattr(cfg, "img_diag_scope_validate_sql", None) or "").strip()
    return custom or _DEFAULT_VALIDATE_SQL


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


def bind_scope_validate_sql(sql_template: str, scope: dict[str, Any]) -> str:
    """将 :boiler / :device_name / :check_location_name / :row_no / :tube_no 替换为 SQL 字面量。"""
    scope = scope_dict_for_validate(scope)
    boiler = scope.get("boiler") or ""
    device = scope.get("device_name") or ""
    location = scope.get("check_location_name") or ""
    row_no = scope.get("row_no")
    tube_no = scope.get("tube_no")
    sql = sql_template
    sql = sql.replace(":boiler", _sql_literal(str(boiler) if boiler else None))
    sql = sql.replace(":device_name", _sql_literal(str(device) if device else None))
    sql = sql.replace(
        ":check_location_name",
        _sql_literal(str(location) if location else None),
    )
    sql = sql.replace(":row_no", _sql_int_literal(row_no if isinstance(row_no, int) else None))
    sql = sql.replace(":tube_no", _sql_int_literal(tube_no if isinstance(tube_no, int) else None))
    if ":piperow_name" in sql:
        sql = sql.replace(":piperow_name", _sql_literal(None))
    return sql


async def validate_scope_in_catalog(
    scope: dict[str, Any],
    *,
    executor: Any | None = None,
) -> tuple[int, str | None]:
    """执行库表校验 SQL，返回 (record_count, error_message)。"""
    cfg = get_app_config().analysis
    skip_on_error = bool(getattr(cfg, "img_diag_scope_validate_skip_on_error", False))
    scope = scope_dict_for_validate(scope)
    if not scope.get("boiler") or not scope.get("device_name"):
        return 0, "missing_boiler_or_device"

    sql_tpl = default_scope_validate_sql()
    sql = bind_scope_validate_sql(sql_tpl, scope)
    if executor is None:
        from app.nl2sql.executor import SQLExecutor

        executor = SQLExecutor()

    timeout = float(getattr(cfg, "img_diag_scope_validate_timeout_s", 10.0))
    try:
        import asyncio

        rows = await asyncio.wait_for(executor.execute(sql), timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        logger.warning("img_diag scope validate SQL failed: %s", exc)
        if skip_on_error:
            return 1, None
        return 0, str(exc)

    if not rows:
        return 0, None
    row = rows[0]
    count = row.get("record_count", row.get("COUNT(*)", 0))
    try:
        return int(count), None
    except (TypeError, ValueError):
        return 0, "invalid_record_count"


async def validate_scope_with_relaxation(
    scope: dict[str, Any],
    *,
    allow_auto_relax: bool,
    executor: Any | None = None,
) -> tuple[int, dict[str, Any], list[str], str | None]:
    """
    校验 scope；allow_auto_relax 为 True 时按 tube→row→location 逐级放宽直至命中或仅剩机组+受热面。

    返回 (count, effective_scope, relaxed_fields, error_message)。
    """
    current = scope_dict_for_validate(scope)
    relaxed_fields: list[str] = []

    while True:
        count, err = await validate_scope_in_catalog(current, executor=executor)
        if count > 0:
            return count, current, relaxed_fields, err
        if not allow_auto_relax:
            return count, current, relaxed_fields, err
        next_scope, dropped = relax_scope_one_level(current)
        if dropped is None:
            return count, current, relaxed_fields, err
        relaxed_fields.append(dropped)
        current = next_scope
