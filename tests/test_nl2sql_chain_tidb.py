from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.schema_service import TableColumn, TableSchema
from app.nl2sql.validator import SQLValidator


class _FakeSchema:
    def __init__(self, tables: list[TableSchema]) -> None:
        self._tables = tables

    def list_tables(self) -> list[TableSchema]:
        return self._tables


def _build_chain_for_unit() -> NL2SQLChain:
    chain = object.__new__(NL2SQLChain)
    chain._validator = SQLValidator()
    chain._tidb_forbidden_aliases = set(NL2SQLChain._tidb_forbidden_aliases_default)
    chain._schema = _FakeSchema(
        [
            TableSchema(
                name="monitor_hotarea_temp",
                columns=[TableColumn("id", "BIGINT"), TableColumn("boiler_id", "BIGINT"), TableColumn("point_id", "BIGINT")],
                foreign_keys=[("boiler_id", "account_boiler", "id"), ("point_id", "base_temp_point", "id")],
            ),
            TableSchema(
                name="account_boiler",
                columns=[TableColumn("id", "BIGINT"), TableColumn("boiler_name", "VARCHAR")],
                foreign_keys=[],
            ),
            TableSchema(
                name="base_temp_point",
                columns=[TableColumn("id", "BIGINT"), TableColumn("point_name", "VARCHAR")],
                foreign_keys=[],
            ),
        ]
    )
    return chain


def test_tidb_rewrite_alias_and_postgres_interval() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT t.temp AS load, t.ts AS row_number FROM monitor_hotarea_temp t "
        "WHERE t.ts >= NOW() - INTERVAL '7 days'"
    )
    rewritten, notes = chain._rewrite_tidb_compatible_sql(sql)
    assert " AS load_alias" in rewritten
    assert " AS row_number_alias" in rewritten
    assert "INTERVAL 7 DAY" in rewritten
    assert notes


def test_tidb_validate_forbidden_alias() -> None:
    chain = _build_chain_for_unit()
    ok, reason = chain._validate_tidb_dialect("SELECT temp AS load FROM monitor_hotarea_temp")
    assert not ok
    assert reason is not None
    assert "forbidden alias" in reason


def test_tidb_validate_forbidden_window_function() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT LAG(temp) OVER (PARTITION BY boiler_id ORDER BY ts) AS prev_temp FROM monitor_hotarea_temp"
    ok, reason = chain._validate_tidb_dialect(sql)
    assert not ok
    assert reason is not None
    assert "window functions" in reason


def test_tidb_validate_forbidden_postgres_interval() -> None:
    chain = _build_chain_for_unit()
    ok, reason = chain._validate_tidb_dialect(
        "SELECT * FROM monitor_hotarea_temp WHERE ts >= NOW() - INTERVAL '7 days'"
    )
    assert not ok
    assert reason is not None
    assert "postgres interval" in reason.lower()


def test_tidb_forbidden_aliases_env_extend(monkeypatch) -> None:
    monkeypatch.setenv("NL2SQL_TIDB_FORBIDDEN_ALIASES", "foo_alias,bar_alias")
    chain = _build_chain_for_unit()
    chain._tidb_forbidden_aliases = chain._load_tidb_forbidden_aliases_from_env()
    ok, reason = chain._validate_tidb_dialect("SELECT temp AS foo_alias FROM monitor_hotarea_temp")
    assert not ok
    assert reason is not None
    assert "foo_alias" in reason


def test_rewrite_recent_week_time_window() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp "
        "WHERE event_time BETWEEN '2024-01-01 00:00:00' AND '2024-01-07 23:59:59'"
    )
    rewritten, notes = chain._rewrite_query_filters(sql, question="请分析近一周超温原因")
    assert "DATE_SUB(NOW(), INTERVAL 7 DAY)" in rewritten
    assert "NOW()" in rewritten
    assert notes


def test_rewrite_region_equals_to_like() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE area = 'front wall'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="analyze front wall overheat")
    assert "area LIKE '%front wall%'" in rewritten
    assert notes


def test_rewrite_today_time_window() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE event_time >= '2024-01-01 00:00:00'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="今天超温情况")
    assert "event_time >= CURDATE()" in rewritten
    assert notes


def test_rewrite_last_year_time_window() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE collect_time BETWEEN '2023-01-01' AND '2023-12-31'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="请分析去年超温趋势")
    assert "DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR)" in rewritten
    assert "DATE_FORMAT(CURDATE(), '%Y-01-01')" in rewritten
    assert notes


def test_rewrite_recent_30_days_time_window() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE ts = '2024-02-01'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="recent 30 days overheat")
    assert "DATE_SUB(NOW(), INTERVAL 30 DAY)" in rewritten
    assert "ts <=" in rewritten
    assert notes


def test_table_scope_from_env(monkeypatch) -> None:
    chain = _build_chain_for_unit()
    tc = {
        "monitor_hotarea_temp": {"id", "boiler_id"},
        "account_boiler": {"id", "boiler_name"},
        "base_temp_point": {"id", "point_name"},
    }
    monkeypatch.setenv("ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT", "monitor_hotarea_temp,account_boiler")
    scoped = chain._resolve_table_scope(analysis_type="overheat_guidance", table_columns=tc)
    assert scoped == {"monitor_hotarea_temp", "account_boiler"}


def test_join_whitelist_rejects_unknown_join() -> None:
    chain = _build_chain_for_unit()
    tc = {
        "monitor_hotarea_temp": {"id", "boiler_id", "point_id"},
        "account_boiler": {"id", "boiler_name"},
        "base_temp_point": {"id", "point_name"},
    }
    wl = chain._build_join_whitelist(tc, analysis_type="overheat_guidance")
    ok, reason = chain._validate_join_whitelist(
        "SELECT 1 FROM monitor_hotarea_temp t JOIN account_boiler b ON t.id = b.id",
        tc,
        wl,
    )
    assert not ok
    assert reason is not None
    assert "join key not in whitelist" in reason
