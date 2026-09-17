"""suggested_filters 强制改写单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.nl2sql.suggested_filters_rewrite import rewrite_sql_with_suggested_filters


def test_replace_truncated_name_in_with_full_coverage() -> None:
    sql = (
        "SELECT s.name, s.area FROM t_station AS s "
        "WHERE s.name IN ('F1(王四营)','F2(望京)','F3(天竺)','F4(八仙庄)','F5(平各庄)') "
        "AND s.area LIKE '%朝阳区%'"
    )
    coverage = [
        "F1(王四营)",
        "F2(望京)",
        "F3(天竺)",
        "F27(大鲁店)",
        "F28(金盏)",
        "F29(东窑)",
    ]
    filters = [
        {
            "table": "t_station",
            "column": "area",
            "op": "=",
            "value": "朝阳区",
            "source": "semantic",
        },
        {
            "table": "t_station",
            "column": "name",
            "op": "in",
            "value": coverage,
            "source": "device_station_map",
        },
    ]
    out, notes = rewrite_sql_with_suggested_filters(sql, filters)
    assert "F27(大鲁店)" in out
    assert "F28(金盏)" in out
    assert "F29(东窑)" in out
    assert "F1(王四营)" in out
    assert "s.area = '朝阳区'" in out or "area = '朝阳区'" in out
    assert "area LIKE" not in out
    assert any(n.startswith("suggested_filter_replace:device_station_map:name") for n in notes)
    assert any(n.startswith("suggested_filter_replace:semantic:area") for n in notes)


def test_inject_station_name_in_when_missing() -> None:
    sql = (
        "SELECT f.project_name, f.total_settle FROM t_data_wash_fcb AS f "
        "JOIN t_station AS s ON f.project_name = s.name "
        "WHERE s.area = '朝阳区'"
    )
    filters = [
        {
            "table": "t_data_wash_fcb",
            "column": "station_name",
            "op": "in",
            "value": ["F1-7", "F27-8"],
            "source": "fcb_layer0",
        },
        {
            "table": "t_station",
            "column": "area",
            "op": "=",
            "value": "朝阳区",
            "source": "semantic",
        },
    ]
    out, notes = rewrite_sql_with_suggested_filters(sql, filters)
    assert "station_name IN ('F1-7','F27-8')" in out.replace(" ", "") or (
        "f.station_name IN ('F1-7','F27-8')" in out
    )
    assert any(n.startswith("suggested_filter_inject:fcb_layer0:station_name") for n in notes)


def test_named_project_overrides_not_expand_when_only_semantic_eq() -> None:
    sql = "SELECT * FROM t_data_wash_fcb WHERE project_name = 'F8(周村)'"
    filters = [
        {
            "table": "t_data_wash_fcb",
            "column": "project_name",
            "op": "=",
            "value": "F8(周村)",
            "source": "semantic",
        }
    ]
    out, notes = rewrite_sql_with_suggested_filters(sql, filters)
    assert "project_name = 'F8(周村)'" in out
    assert any("suggested_filter_replace:semantic:project_name" in n for n in notes)


def test_empty_in_skipped() -> None:
    sql = "SELECT * FROM t_data_wash_gnss g JOIN t_station s ON g.project_name=s.name WHERE s.area='通州区'"
    filters = [
        {
            "table": "t_data_wash_gnss",
            "column": "project_name",
            "op": "in",
            "value": [],
            "source": "device_station_map",
        }
    ]
    out, notes = rewrite_sql_with_suggested_filters(sql, filters)
    assert out == sql
    assert notes == []
    assert "IN ()" not in out


def test_e2e_chaoyang_dxswj_catalog_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    """朝阳区地下水站点清单：链接全量覆盖 + 改写后 IN 含 F27/F28/F29。"""
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.nl2sql.nl2sql_business_profile import (
        clear_nl2sql_business_profile_cache,
        get_nl2sql_business_profile,
    )
    from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
    from app.nl2sql.schema_linker import link_schema
    from app.nl2sql.semantic_layer import align_semantics, clear_semantic_assets_cache, load_semantic_assets

    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    q = "请帮我查询朝阳区的地下水监测站点有哪些"
    intent = QuestionIntent(
        raw_question=q,
        scope_question=q,
        time_window=None,
        scope=QuestionScopeIntent(district="朝阳区", device_type="dxswj"),
    )
    binding = align_semantics(q, intent, assets=assets)
    assert binding is not None
    assert "dxswj" in binding.device_types
    assert len(binding.device_coverage_project_names) == 41

    table_columns = {
        "t_data_wash_dxswj": {"deep", "data_time", "project_name"},
        "t_station": {"name", "area", "code", "lon", "lat"},
    }
    linked = link_schema(q, intent, binding, table_columns, allowlist=set(table_columns), assets=assets)
    name_f = [
        f
        for f in linked.suggested_filters
        if f.get("column") == "name" and f.get("source") == "device_station_map"
    ]
    assert name_f and len(name_f[0]["value"]) == 41

    bad_sql = (
        "SELECT s.name, s.code, s.area, s.lon, s.lat FROM t_station AS s "
        "WHERE s.name IN ('F1(王四营)','F2(望京)','F3(天竺)','F4(八仙庄)','F5(平各庄)') "
        "AND s.area LIKE '%朝阳区%'"
    )
    fixed, notes = rewrite_sql_with_suggested_filters(bad_sql, linked.suggested_filters)
    assert "F27(大鲁店)" in fixed
    assert "F28(金盏)" in fixed
    assert "F29(东窑)" in fixed
    assert any("device_station_map:name" in n for n in notes)
