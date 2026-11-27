"""周期窗列表确定性 SQL：PostgreSQL ROW_NUMBER，禁止 QUALIFY。"""

from __future__ import annotations

from datetime import datetime

from app.data_query_agent.catalog import get_library_catalog
from app.data_query_agent.period_sql import build_period_list_sql
from app.data_query_agent.scope_intent import resolve_scope_intent
from app.nl2sql.chain import NL2SQLChain


def test_period_sql_fcb_month_window_uses_row_number() -> None:
    lib = get_library_catalog().get("fcb")
    assert lib is not None
    scope = resolve_scope_intent("请帮我查询F2(望京)最近一个月的沉降量", lib)
    assert scope.confirmed_scope.get("station_id") == "F2(望京)"
    sql, params = build_period_list_sql(lib, scope, max_rows=50)
    low = sql.lower()
    assert "qualify" not in low
    assert "row_number() over" in low
    assert "t_data_wash_fcb" in low
    assert "any(:rep_marks)" in low
    assert "any(:projects)" in low
    assert "partition by f.station_name" in low
    assert "partition by f.project_name" not in low
    assert "f2-7" in [str(x).lower() for x in params["rep_marks"]]
    assert any("F2(望京)" in str(p) for p in params["projects"])
    assert "f.data_time >= :t_start" in sql
    assert isinstance(params["t_start"], datetime)
    assert isinstance(params["t_end"], datetime)
    assert "ORDER BY ABS(" in sql
    assert "ABS(period" not in sql
    assert "ABS(annual_settle_mm)" not in sql
    assert "AS period_settle_mm" in sql
    assert "AS annual_settle_mm" not in sql
    # Δ = 窗末(rn_desc) − 窗初(rn_asc)；负沉正弹
    assert (
        "MAX(CASE WHEN w.rn_desc = 1 THEN w.v END) - MAX(CASE WHEN w.rn_asc = 1 THEN w.v END)"
        in sql
    )
    assert (
        "MAX(CASE WHEN w.rn_asc = 1 THEN w.v END) - MAX(CASE WHEN w.rn_desc = 1 THEN w.v END)"
        not in sql
    )
    assert lib.period_key == "period_settle_mm"
    assert params["limit"] == 50


def test_period_sql_kxsylj_all_devices_no_rep_marks() -> None:
    lib = get_library_catalog().get("kxsylj")
    assert lib is not None
    scope = resolve_scope_intent("请帮我查询F8(周村)最近一个月的孔隙水压力变化量", lib)
    sql, params = build_period_list_sql(lib, scope, max_rows=50)
    low = sql.lower()
    assert "t_data_wash_kxsylj" in low
    assert "partition by f.station_name" in low
    assert "any(:rep_marks)" not in low
    assert "rep_marks" not in params
    assert "qualify" not in low


def test_period_sql_gnss_filters_station_name() -> None:
    lib = get_library_catalog().get("gnss")
    assert lib is not None
    scope = resolve_scope_intent("请帮我查询F15-GNSS最近一个月的位移", lib)
    sql, params = build_period_list_sql(lib, scope, max_rows=50)
    low = sql.lower()
    assert "t_data_wash_gnss" in low
    assert "partition by f.station_name" in low
    assert "f.station_name = any(:projects)" in low
    assert "f.project_name = any(:projects)" not in low
    assert "F15-GNSS" in params.get("projects") or []
    assert "qualify" not in low


def test_period_sql_qxz_filters_station_code() -> None:
    lib = get_library_catalog().get("qxz")
    assert lib is not None
    scope = resolve_scope_intent("请帮我查询F21(西集)最近一个月的气温", lib)
    sql, params = build_period_list_sql(lib, scope, max_rows=50)
    low = sql.lower()
    assert "t_data_wash_qxz" in low
    assert "partition by f.station_name" in low
    assert "split_part" in low
    assert params.get("site_codes") == ["F21"]
    assert "f.project_name = any(:projects)" not in low
    assert "s.name = w.project_name" not in low.replace(" ", "")


def test_postgres_validator_rejects_qualify() -> None:
    class _Norm:
        def normalize_sql(self, sql: str) -> str:
            return sql

    chain = NL2SQLChain.__new__(NL2SQLChain)
    chain._validator = _Norm()
    ok, reason = chain._validate_postgres_dialect(
        "SELECT 1 FROM t_data_wash_fcb QUALIFY ROW_NUMBER() OVER (ORDER BY data_time) = 1"
    )
    assert ok is False
    assert reason and "QUALIFY" in reason
