"""V2-P2：SQLExecutor 绑定参数，禁止拼接。"""

from __future__ import annotations

import inspect
from datetime import datetime
from pathlib import Path

import pytest

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


def test_resolved_sql_params_bind_naive_datetime() -> None:
    params = resolved_sql_params(
        {"t_start": "2026-09-17T00:00:00", "t_end": "2026-09-18T00:00:00", "area": None}
    )
    assert params["t_start"] == datetime(2026, 9, 17, 0, 0, 0)
    assert params["t_end"] == datetime(2026, 9, 18, 0, 0, 0)
    assert params["t_start"].tzinfo is None
    assert params["t_end"].tzinfo is None
    assert params["area"] is None


def test_resolved_sql_params_extra_datetime_not_restringified() -> None:
    start = datetime(2026, 4, 1, 8, 0, 0)
    params = resolved_sql_params(
        {"t_start": "2026-01-01T00:00:00", "t_end": "2026-01-02T00:00:00"},
        extra={"t_start": start, "t_end": "2026-07-01 00:00:00"},
    )
    assert params["t_start"] == datetime(2026, 4, 1, 8, 0, 0)
    assert params["t_end"] == datetime(2026, 7, 1, 0, 0, 0)


def test_resolved_sql_params_rejects_empty_window() -> None:
    with pytest.raises(ValueError, match="sql_bind_missing_t_start"):
        resolved_sql_params({"t_start": "", "t_end": "2026-09-18T00:00:00"})
