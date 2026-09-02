"""NL2SQL 可选 forced_tables：锁表时收窄 catalog；不传则现网行为。"""

from __future__ import annotations

import pytest

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache
from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
from app.nl2sql.schema_linker import link_schema
from app.nl2sql.semantic_layer import align_semantics, clear_semantic_assets_cache, load_semantic_assets
from app.models.nl2sql import NL2SQLQueryRequest


@pytest.fixture(autouse=True)
def _clear_nl2sql_caches() -> None:
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()
    yield
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()


def _assets(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    clear_nl2sql_business_profile_cache()
    from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile

    profile = get_nl2sql_business_profile()
    assert profile is not None
    from pathlib import Path

    return load_semantic_assets(str((Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve()))


def test_nl2sql_request_forced_tables_optional() -> None:
    req = NL2SQLQueryRequest(user_id="u1", session_id="s1", question="监测点沉降")
    assert req.forced_tables is None


def test_align_semantics_without_forced_defaults_fcb(monkeypatch: pytest.MonkeyPatch) -> None:
    assets = _assets(monkeypatch)
    intent = QuestionIntent(
        raw_question="朝阳区沉降大的点",
        scope_question="朝阳区沉降大的点",
        time_window=None,
        scope=QuestionScopeIntent(),
    )
    binding = align_semantics("朝阳区沉降大的点", intent, assets=assets)
    assert binding is not None
    assert "fcb" in binding.device_types
    assert any("fcb" in str(t).lower() for t in binding.device_type_tables)


def test_align_semantics_forced_jyb_ignores_settle_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    assets = _assets(monkeypatch)
    intent = QuestionIntent(
        raw_question="朝阳区沉降大的点",
        scope_question="朝阳区沉降大的点",
        time_window=None,
        scope=QuestionScopeIntent(device_type="jyb"),
    )
    binding = align_semantics(
        "朝阳区沉降大的点",
        intent,
        assets=assets,
        forced_tables=["t_data_wash_jyb"],
    )
    assert binding is not None
    assert binding.device_types == ["jyb"]
    assert "t_data_wash_jyb" in {t.lower() for t in binding.device_type_tables}
    assert "t_data_wash_fcb" not in {t.lower() for t in binding.device_type_tables}
    assert "forced_tables_pin" in binding.warnings


def test_link_schema_forced_jyb_not_fcb(monkeypatch: pytest.MonkeyPatch) -> None:
    assets = _assets(monkeypatch)
    intent = QuestionIntent(
        raw_question="朝阳区沉降大的点",
        scope_question="朝阳区沉降大的点",
        time_window=None,
        scope=QuestionScopeIntent(device_type="jyb"),
    )
    binding = align_semantics(
        "朝阳区沉降大的点",
        intent,
        assets=assets,
        forced_tables=["t_data_wash_jyb"],
    )
    table_columns = {
        "t_data_wash_fcb": {"total_settle", "data_time", "station_id", "project_name"},
        "t_data_wash_jyb": {"total_settle", "data_time", "station_id", "project_name"},
        "t_station": {"name", "area"},
    }
    linked = link_schema(
        "朝阳区沉降大的点",
        intent,
        binding,
        table_columns,
        allowlist=set(table_columns.keys()),
        assets=assets,
        forced_tables=["t_data_wash_jyb"],
    )
    names = {t.name.lower() for t in linked.tables}
    assert "t_data_wash_jyb" in names
    assert "t_data_wash_fcb" not in names
    assert linked.status != "failed"


def test_link_schema_forced_unknown_table_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    assets = _assets(monkeypatch)
    intent = QuestionIntent(
        raw_question="监测点沉降",
        scope_question="监测点沉降",
        time_window=None,
        scope=QuestionScopeIntent(),
    )
    binding = align_semantics("监测点沉降", intent, assets=assets, forced_tables=["not_a_table"])
    table_columns = {
        "t_data_wash_fcb": {"total_settle", "data_time"},
        "t_station": {"name"},
    }
    linked = link_schema(
        "监测点沉降",
        intent,
        binding,
        table_columns,
        allowlist=set(table_columns.keys()),
        assets=assets,
        forced_tables=["not_a_table"],
    )
    assert linked.status == "failed"
    assert linked.fail_reason == "forced_table_not_in_allowlist"


def test_resolve_table_scope_forced_unions_station(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT", raising=False)
    monkeypatch.delenv("NL2SQL_BUSINESS_DOMAIN", raising=False)
    clear_nl2sql_business_profile_cache()
    chain = object.__new__(NL2SQLChain)
    tc = {
        "t_data_wash_fcb": {"total_settle"},
        "t_data_wash_jyb": {"total_settle"},
        "t_station": {"name", "area"},
    }
    scoped = chain._resolve_table_scope(
        analysis_type="data_query",
        table_columns=tc,
        forced_tables=["t_data_wash_jyb"],
    )
    assert scoped == {"t_data_wash_jyb", "t_station"} or scoped == {"t_data_wash_jyb"}
    if "t_station" in tc:
        assert "t_station" in scoped
    assert "t_data_wash_fcb" not in scoped


def test_resolve_table_scope_without_forced_unchanged() -> None:
    chain = object.__new__(NL2SQLChain)
    tc = {
        "t_data_wash_fcb": {"total_settle"},
        "t_data_wash_jyb": {"total_settle"},
        "t_station": {"name"},
    }
    a = chain._resolve_table_scope(analysis_type="data_query", table_columns=tc)
    b = chain._resolve_table_scope(analysis_type="data_query", table_columns=tc, forced_tables=None)
    c = chain._resolve_table_scope(analysis_type="data_query", table_columns=tc, forced_tables=[])
    assert a == b == c
