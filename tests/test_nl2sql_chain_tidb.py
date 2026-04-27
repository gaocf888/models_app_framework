from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.validator import SQLValidator


def _build_chain_for_unit() -> NL2SQLChain:
    chain = object.__new__(NL2SQLChain)
    chain._validator = SQLValidator()
    chain._tidb_forbidden_aliases = set(NL2SQLChain._tidb_forbidden_aliases_default)
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
