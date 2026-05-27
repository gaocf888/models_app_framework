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


def test_rewrite_dynamic_time_window_does_not_corrupt_nested_date_sub() -> None:
    """SQL 已含 DATE_SUB 表达式时仅改写字面量，不截断嵌套括号。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT 1 FROM monitor_hotarea_temp mht "
        "WHERE mht.start_time >= DATE_SUB(NOW(), INTERVAL 7 DAY) ORDER BY mht.start_time DESC"
    )
    rewritten, _notes = chain._rewrite_query_filters(sql, question="请分析最近一周超温情况")
    assert ", INTERVAL 7 DAY), INTERVAL" not in rewritten
    assert rewritten.count("INTERVAL 7 DAY") == 1
    assert "mht.start_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)" in rewritten


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
    assert "ts <" in rewritten
    assert notes


def test_rewrite_today_qa_literal_half_open() -> None:
    """QA 存库为固定日期字面量时，问句「今天」应改写为自然日半开区间。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2026-05-27 00:00:00' "
        "AND t.start_time <= '2026-05-27 23:59:59'"
    )
    q = "请分析1号锅炉今天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(sql, question=q, time_intent_source=q)
    assert "t.start_time >= CURDATE()" in rewritten
    assert "t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert "23:59:59" not in rewritten
    assert notes


def test_rewrite_today_qa_literal_lt_upper_bound() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2026-05-27 00:00:00' "
        "AND t.start_time < '2026-05-28 00:00:00'"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql, question="今天超温", time_intent_source="今天超温"
    )
    assert "t.start_time >= CURDATE()" in rewritten
    assert "t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert "2026-05-28" not in rewritten
    assert notes


def test_rewrite_plan_near_year_overrides_today() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.record_time >= '2020-01-01 00:00:00' "
        "AND t.record_time <= '2026-05-27 23:59:59'"
    )
    q = "请分析1号锅炉今天的超温。查询用户指定锅炉近一年内的检修记录"
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=q, time_intent_source="请分析1号锅炉今天的超温"
    )
    assert "DATE_SUB(CURDATE(), INTERVAL 1 YEAR)" in rewritten
    assert "record_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert "CURDATE()" in rewritten
    assert "record_time >= CURDATE()" not in rewritten
    assert notes


def test_rewrite_entity_scope_boiler_from_question() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "JOIN account_boiler ab ON t.boiler_id = ab.id "
        "WHERE ab.boiler_name = '2号锅炉'"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql, question="请分析3号锅炉今天的超温", time_intent_source="请分析3号锅炉今天的超温"
    )
    assert "ab.boiler_name = '3号锅炉'" in rewritten
    assert "entity_scope_boiler_name" in notes


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
