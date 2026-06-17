"""看图诊断 scope 库表 existence 校验（第二层）。"""

from __future__ import annotations

from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_VALIDATE_SQL = """
SELECT COUNT(*) AS record_count
FROM account_boiler ab
INNER JOIN account_static_device asd ON ab.boiler_id = asd.boiler_id
LEFT JOIN account_device_piperow adp ON asd.device_id = adp.device_id
WHERE ab.boiler_name = :boiler
  AND asd.device_name = :device_name
  AND (:piperow_name IS NULL OR :piperow_name = '' OR adp.piperow_name = :piperow_name)
""".strip()

# 与 .env 中 ANALYSIS_IMG_DIAG_SCOPE_VALIDATE_SQL 默认值保持一致（单行写法见 app-deploy/.env.example）
DEFAULT_IMG_DIAG_SCOPE_VALIDATE_SQL = (
    "SELECT COUNT(*) AS record_count FROM account_boiler ab "
    "INNER JOIN account_static_device asd ON ab.boiler_id = asd.boiler_id "
    "LEFT JOIN account_device_piperow adp ON asd.device_id = adp.device_id "
    "WHERE ab.boiler_name = :boiler AND asd.device_name = :device_name "
    "AND (:piperow_name IS NULL OR :piperow_name = '' OR adp.piperow_name = :piperow_name)"
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


def bind_scope_validate_sql(sql_template: str, scope: dict[str, Any]) -> str:
    """将 :boiler 等占位符替换为安全 SQL 字面量（只读 COUNT）。"""
    boiler = scope.get("boiler") or ""
    device = scope.get("device_name") or ""
    piperow = scope.get("piperow_name") or ""
    sql = sql_template
    sql = sql.replace(":boiler", _sql_literal(str(boiler) if boiler else None))
    sql = sql.replace(":device_name", _sql_literal(str(device) if device else None))
    piperow_lit = _sql_literal(str(piperow) if piperow else None)
    sql = sql.replace(":piperow_name", piperow_lit)
    return sql


async def validate_scope_in_catalog(
    scope: dict[str, Any],
    *,
    executor: Any | None = None,
) -> tuple[int, str | None]:
    """
    执行库表校验 SQL，返回 (record_count, error_message)。
    error_message 非空表示执行异常（非 0 行）。
    """
    cfg = get_app_config().analysis
    skip_on_error = bool(getattr(cfg, "img_diag_scope_validate_skip_on_error", False))
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
