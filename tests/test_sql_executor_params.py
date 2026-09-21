"""V2-P2：SQLExecutor 绑定参数，禁止拼接。"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.analysis_agent.deterministics.fcb_endpoints import load_sql_template, resolved_sql_params
from app.nl2sql.executor import SQLExecutor


def test_execute_accepts_optional_params() -> None:
    sig = inspect.signature(SQLExecutor.execute)
    assert "params" in sig.parameters
    assert sig.parameters["params"].default is None


def test_endpoints_sql_uses_bind_placeholders() -> None:
    sql = load_sql_template("fcb_period_endpoints.sql")
    assert ":t_start" in sql and ":t_end" in sql
    assert ":area" in sql
    assert ":fcb_marks" in sql
    assert "{area}" not in sql
    assert "ANY(" in sql
    # asyncpg：裸 `:area IS NULL` 在 area=None 时无法推断 $n 类型
    assert ":area IS NULL" not in sql
    assert "CAST(:area AS text) IS NULL" in sql


def test_typical_fcb_sql_casts_null_area() -> None:
    sql = load_sql_template("typical_series_fcb.sql")
    assert ":area IS NULL" not in sql
    assert "CAST(:area AS text) IS NULL" in sql


def test_resolved_sql_params_do_not_format_sql() -> None:
    params = resolved_sql_params(
        {"t_start": "2026-04-01T00:00:00", "t_end": "2026-07-01T00:00:00", "area": "朝阳区"}
    )
    assert params["area"] == "朝阳区"
    assert isinstance(params["fcb_marks"], list)
    sql = Path(__file__).resolve().parents[1] / "configs/analysis_agent_reports/sql/fcb_period_endpoints.sql"
    text = sql.read_text(encoding="utf-8")
    assert "朝阳区" not in text
