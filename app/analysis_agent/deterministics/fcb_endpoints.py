"""窗初/窗末确定性 SQL：读模板 + 绑定覆盖名单 ∩ 层位字典，走 SQLExecutor（无生成器）。"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.analysis_agent.deterministics.coverage import (
    endpoint_bind_lists,
    layer0_mark_keys,
    project_names,
)
from app.analysis_agent.period import PeriodValidationError, _parse_datetime
from app.core.logging import get_logger
from app.nl2sql.executor import SQLExecutor

logger = get_logger(__name__)

_SQL_DIR = Path(__file__).resolve().parents[3] / "configs" / "analysis_agent_reports" / "sql"


def _bind_timestamp(value: Any, *, key: str) -> datetime:
    """asyncpg 将 CAST(:t AS timestamp) 的参数编成 timestamp，必须绑 naive datetime，不能绑 ISO 字符串。"""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"sql_bind_missing_{key}")
    try:
        return _parse_datetime(raw, as_end_date=False)
    except PeriodValidationError as exc:
        raise ValueError(f"sql_bind_invalid_{key}:{raw}") from exc


def resolved_sql_params(
    resolved: dict[str, Any],
    extra: dict[str, Any] | None = None,
    *,
    sql_file: str = "",
) -> dict[str, Any]:
    """绑定 _resolved + 字典 allowlist；禁止把未校验 area 拼进 SQL 字符串。"""
    lists = endpoint_bind_lists()
    params: dict[str, Any] = {
        "t_start": resolved.get("t_start"),
        "t_end": resolved.get("t_end"),
        "area": resolved.get("area"),
        **lists,
        "dxswj_projects": list(project_names("dxswj")),
        "qxz_projects": list(project_names("qxz")),
        "kxsylj_projects": list(project_names("kxsylj")),
        "gnss_projects": list(project_names("gnss")),
    }
    # 典型曲线左轴只要层位 0，避免把压缩层边界标日序列混进来
    if (sql_file or "").strip() == "typical_series_fcb.sql":
        keys = layer0_mark_keys()
        params["fcb_marks"] = [mark for _project, mark in keys]
        params["fcb_projects"] = list(dict.fromkeys(project for project, _mark in keys))
    if extra:
        params.update(extra)
    params["t_start"] = _bind_timestamp(params.get("t_start"), key="t_start")
    params["t_end"] = _bind_timestamp(params.get("t_end"), key="t_end")
    return params


def load_sql_template(sql_file: str) -> str:
    name = (sql_file or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"invalid_sql_file:{sql_file}")
    path = _SQL_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"sql_template_missing:{path}")
    return path.read_text(encoding="utf-8")


async def run_sql_template(
    *,
    sql_file: str,
    resolved: dict[str, Any],
    executor: SQLExecutor,
    extra_params: dict[str, Any] | None = None,
    max_rows: int = 20000,
) -> tuple[list[dict[str, Any]], str]:
    sql = load_sql_template(sql_file)
    params = resolved_sql_params(resolved, extra_params, sql_file=sql_file)
    # 监测方式专用时序：用 extra 覆盖 projects 列表
    rows = await executor.execute(sql, params)
    if max_rows > 0:
        rows = rows[:max_rows]
    logger.info(
        "sql_template done file=%s rows=%s t_start=%s t_end=%s area=%s",
        sql_file,
        len(rows),
        params.get("t_start"),
        params.get("t_end"),
        params.get("area"),
    )
    return rows, sql
