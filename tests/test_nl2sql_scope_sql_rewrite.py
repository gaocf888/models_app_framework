"""Phase 2：scope SQL 占位符改写。"""

from __future__ import annotations

import pytest

from app.core.config import get_app_config
from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.scope_sql_rewrite import rewrite_scope_sql_placeholders
from tests.test_nl2sql_chain_tidb import _build_chain_for_unit


@pytest.fixture(autouse=True)
def _clear_nl2sql_intent_config_cache() -> None:
    get_app_config.cache_clear()
    yield  # type: ignore[misc]
    get_app_config.cache_clear()


def _scope_sql_template() -> str:
    return (
        "SELECT asd.device_name, adp.piperow_name, adp.row_count, adp.pipe_count "
        "FROM account_static_device asd "
        "INNER JOIN account_device_piperow adp ON asd.id = adp.device_id "
        "WHERE (@device_keyword IS NULL OR @device_keyword = '' "
        "OR asd.device_name LIKE CONCAT('%', @device_keyword, '%')) "
        "AND (@piperow_keyword IS NULL OR @piperow_keyword = '' "
        "OR adp.piperow_name LIKE CONCAT('%', @piperow_keyword, '%')) "
        "AND (@row_no IS NULL OR adp.row_count = @row_no) "
        "AND (@tube_no IS NULL OR adp.pipe_count = @tube_no)"
    )


def test_scope_placeholder_rewrite_disabled_by_default() -> None:
    sql = _scope_sql_template()
    scopes = {
        "device_name": "低温过热器",
        "piperow_name": "第一层",
        "row_no": 1,
        "tube_no": 1,
    }
    rewritten, notes = rewrite_scope_sql_placeholders(sql, scopes)
    assert rewritten == sql
    assert notes == []


def test_device_keyword_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    sql = _scope_sql_template()
    scopes = {"device_name": "低温过热器", "piperow_name": None, "row_no": None, "tube_no": None}
    rewritten, notes = rewrite_scope_sql_placeholders(sql, scopes)
    assert "@device_keyword" not in rewritten
    assert "'低温过热器'" in rewritten
    assert "device_keyword_placeholder_single" in notes


def test_device_keyword_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    sql = _scope_sql_template()
    scopes = {"device_name": None, "piperow_name": None, "row_no": None, "tube_no": None}
    rewritten, notes = rewrite_scope_sql_placeholders(sql, scopes)
    assert "'' IS NULL OR '' = ''" in rewritten or "(@device_keyword IS NULL OR @device_keyword = ''" not in rewritten
    assert "@device_keyword" not in rewritten
    assert "'' = ''" in rewritten
    assert "device_keyword_placeholder_empty" in notes


def test_piperow_and_row_tube_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    sql = _scope_sql_template()
    scopes = {
        "device_name": "低温过热器",
        "piperow_name": "第一层",
        "row_no": 1,
        "tube_no": 2,
    }
    rewritten, notes = rewrite_scope_sql_placeholders(sql, scopes)
    assert "@piperow_keyword" not in rewritten
    assert "@row_no" not in rewritten
    assert "@tube_no" not in rewritten
    assert "'第一层'" in rewritten
    assert "adp.row_count = 1" in rewritten
    assert "adp.pipe_count = 2" in rewritten
    assert "piperow_keyword_placeholder_single" in notes
    assert "row_no_placeholder_single" in notes
    assert "tube_no_placeholder_single" in notes


def test_row_tube_null_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    sql = _scope_sql_template()
    scopes = {"device_name": None, "piperow_name": None, "row_no": None, "tube_no": None}
    rewritten, notes = rewrite_scope_sql_placeholders(sql, scopes)
    assert "NULL IS NULL" in rewritten
    assert "row_no_placeholder_skip" in notes
    assert "tube_no_placeholder_skip" in notes


def test_piperow_first_screen_alias_or(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    sql = (
        "SELECT * FROM account_device_piperow adp "
        "WHERE adp.piperow_name LIKE CONCAT('%', @piperow_keyword, '%')"
    )
    scopes = {"piperow_name": "第一屏"}
    rewritten, notes = rewrite_scope_sql_placeholders(sql, scopes)
    assert "@piperow_keyword" not in rewritten
    assert "LIKE CONCAT('%', '第一屏', '%')" in rewritten
    assert "LIKE CONCAT('%', '前屏', '%')" in rewritten
    assert " OR " in rewritten
    assert "piperow_keyword_alias_or" in notes


def test_rewrite_query_filters_scope_placeholders_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    chain = _build_chain_for_unit()
    question = "1号锅炉低温过热器第一层第一排第一根"
    rewritten, notes = chain._rewrite_query_filters(
        _scope_sql_template(),
        question=question,
        time_intent_source=question,
    )
    assert "@device_keyword" not in rewritten
    assert "@piperow_keyword" not in rewritten
    assert "@row_no" not in rewritten
    assert "@tube_no" not in rewritten
    assert "'低温过热器'" in rewritten
    assert "'第一层'" in rewritten
    assert "adp.row_count = 1" in rewritten
    assert "adp.pipe_count = 1" in rewritten
    assert "device_keyword_placeholder_single" in notes


def test_location_keyword_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    sql = (
        "WHERE (@location_keyword IS NULL OR @location_keyword = '' "
        "OR onc.name LIKE CONCAT('%', @location_keyword, '%'))"
    )
    from app.nl2sql.scope_sql_rewrite import rewrite_scope_sql_placeholders

    rewritten, notes = rewrite_scope_sql_placeholders(
        sql, {"check_location_name": None}
    )
    assert "@location_keyword" not in rewritten
    assert "location_keyword_placeholder_empty" in notes


def test_location_keyword_single(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    sql = (
        "WHERE (@location_keyword IS NULL OR @location_keyword = '' "
        "OR onc.name LIKE CONCAT('%', @location_keyword, '%'))"
    )
    from app.nl2sql.scope_sql_rewrite import rewrite_scope_sql_placeholders

    rewritten, notes = rewrite_scope_sql_placeholders(
        sql, {"check_location_name": "出口段"}
    )
    assert "@location_keyword" not in rewritten
    assert "出口段" in rewritten
    assert "location_keyword_placeholder_single" in notes


def test_rewrite_query_filters_scope_disabled_no_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", raising=False)
    chain = _build_chain_for_unit()
    question = "1号锅炉低温过热器第一层第一排第一根"
    rewritten, notes = chain._rewrite_query_filters(
        _scope_sql_template(),
        question=question,
        time_intent_source=question,
    )
    assert "@device_keyword" in rewritten
    assert "@piperow_keyword" in rewritten
    assert "device_keyword_placeholder_single" not in notes


def test_first_screen_question_expands_piperow_or(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    chain = _build_chain_for_unit()
    question = "2号机组屏式过热器前屏第一排第一根"
    rewritten, notes = chain._rewrite_query_filters(
        _scope_sql_template(),
        question=question,
        time_intent_source=question,
    )
    assert "LIKE CONCAT('%', '第一屏', '%')" in rewritten
    assert "LIKE CONCAT('%', '前屏', '%')" in rewritten
    assert "piperow_keyword_alias_or" in notes


def test_chain_extract_scope_literals_still_backward_compatible() -> None:
    chain = object.__new__(NL2SQLChain)
    scopes = chain._extract_scope_literals_from_question("请分析1号锅炉前天的超温")
    assert scopes["unit_keyword"] == "1号锅炉"
    assert scopes["device_name"] is None
