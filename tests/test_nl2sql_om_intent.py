"""运维 om_result 意图与 snapshot 校验单元测试。"""

from __future__ import annotations

import pytest


def test_is_ops_question() -> None:
    from app.nl2sql.om_intent import is_ops_question

    assert is_ops_question("各仪器在线率多少")
    assert is_ops_question("当前有哪些设备告警")
    assert is_ops_question("离线仪器有哪些")
    assert is_ops_question("当前告警设备占比是多少")
    assert is_ops_question("GNSS 离线时长多少天")
    # 裸「告警阈值」偏规范/阈值，不应判运维
    assert not is_ops_question("沉降告警阈值是多少")
    assert not is_ops_question("朝阳区分层标周期沉降")


def test_filter_allowlist_for_question() -> None:
    from app.nl2sql.om_intent import filter_allowlist_for_question

    settle = ("t_station", "t_data_wash_fcb")
    om = ("t_om_device_online_by_type", "t_om_device_alarm_list")
    assert filter_allowlist_for_question(
        "在线率", settlement_tables=settle, om_tables=om
    ) == set(om)
    assert filter_allowlist_for_question(
        "沉降量", settlement_tables=settle, om_tables=om
    ) == set(settle)


def test_qualify_bare_om_tables() -> None:
    from app.nl2sql.om_intent import qualify_bare_om_tables

    sql = "SELECT * FROM t_om_device_alarm_list WHERE 1=1"
    out = qualify_bare_om_tables(sql, om_schema="om_result")
    assert "om_result.t_om_device_alarm_list" in out
    already = "SELECT * FROM om_result.t_om_device_alarm_list"
    assert qualify_bare_om_tables(already) == already


def test_snapshot_filter_detection() -> None:
    from app.nl2sql.om_intent import sql_has_latest_snapshot_filter

    bad = "SELECT * FROM t_om_device_online_by_type"
    assert not sql_has_latest_snapshot_filter(bad)
    # 任意比较不够：须 = (SELECT max(...)) 或 latest CTE + JOIN
    weak = "SELECT * FROM t_om_device_online_by_type WHERE snapshot_time > '2020-01-01'"
    assert not sql_has_latest_snapshot_filter(weak)
    good = (
        "SELECT * FROM t_om_device_online_by_type "
        "WHERE snapshot_time = (SELECT max(snapshot_time) FROM om_result.t_om_data_alarm_summary)"
    )
    assert sql_has_latest_snapshot_filter(good)
    cte = (
        "WITH latest AS (SELECT max(snapshot_time) AS snapshot_time "
        "FROM om_result.t_om_data_alarm_summary) "
        "SELECT o.* FROM om_result.t_om_device_online_by_type o "
        "INNER JOIN latest l ON o.snapshot_time = l.snapshot_time"
    )
    assert sql_has_latest_snapshot_filter(cte)


def test_validator_requires_snapshot_for_om(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("NL2SQL_SQL_DIALECT", "postgres")

    from app.core.config import get_app_config
    from app.nl2sql.schema_routing import clear_schema_routing_cache
    from app.nl2sql.validator import SQLValidator

    get_app_config.cache_clear()
    clear_schema_routing_cache()

    v = SQLValidator()
    ok, err = v.validate_identifiers(
        "SELECT type, online_rate FROM t_om_device_online_by_type",
        allowed_tables={"t_om_device_online_by_type"},
    )
    assert ok is False
    assert err and "snapshot_time" in err

    ok2, err2 = v.validate_identifiers(
        "SELECT type, online_rate FROM om_result.t_om_device_online_by_type "
        "WHERE snapshot_time = (SELECT max(snapshot_time) FROM om_result.t_om_data_alarm_summary)",
        allowed_tables={"t_om_device_online_by_type", "t_om_data_alarm_summary"},
    )
    assert ok2 is True, err2


def test_om_allowlist_loaded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.nl2sql.nl2sql_business_profile import (
        clear_nl2sql_business_profile_cache,
        get_nl2sql_business_profile,
    )

    clear_nl2sql_business_profile_cache()
    p = get_nl2sql_business_profile()
    assert p is not None
    assert "t_om_device_alarm_list" in p.om_result_allowlist
    assert "t_data_wash_fcb" in p.table_allowlist
    assert "t_om_device_alarm_list" not in p.table_allowlist
    combined = p.combined_table_allowlist()
    assert "t_om_device_alarm_list" in combined
    assert "t_data_wash_fcb" in combined


def test_ensure_latest_snapshot_filter_injects() -> None:
    from app.nl2sql.om_intent import ensure_latest_snapshot_filter, sql_has_latest_snapshot_filter

    bare = "SELECT type, online_rate FROM t_om_device_online_by_type"
    out, notes = ensure_latest_snapshot_filter(bare, om_schema="om_result")
    assert "injected_latest_snapshot_filter" in notes
    assert sql_has_latest_snapshot_filter(out)
    assert "om_result.t_om_data_alarm_summary" in out

    already = (
        "SELECT * FROM t_om_device_online_by_type "
        "WHERE snapshot_time = (SELECT max(snapshot_time) FROM om_result.t_om_data_alarm_summary)"
    )
    out2, notes2 = ensure_latest_snapshot_filter(already)
    assert notes2 == []
    assert out2 == already


def test_offline_status_filter_required(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.nl2sql.om_intent import (
        ensure_offline_status_filter,
        sql_has_offline_status_filter,
    )

    bare = (
        "SELECT name FROM om_result.t_om_offline_device_list "
        "WHERE snapshot_time = (SELECT max(snapshot_time) FROM om_result.t_om_data_alarm_summary)"
    )
    assert not sql_has_offline_status_filter(bare)
    out, notes = ensure_offline_status_filter(bare)
    assert "injected_offline_status_eq_0" in notes
    assert sql_has_offline_status_filter(out)
    assert "status = 0" in out

    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("NL2SQL_SQL_DIALECT", "postgres")
    from app.core.config import get_app_config
    from app.nl2sql.schema_routing import clear_schema_routing_cache
    from app.nl2sql.validator import SQLValidator

    get_app_config.cache_clear()
    clear_schema_routing_cache()
    v = SQLValidator()
    ok, err = v.validate_identifiers(
        bare,
        allowed_tables={"t_om_offline_device_list", "t_om_data_alarm_summary"},
    )
    assert ok is False
    assert err and "status" in err

    ok2, err2 = v.validate_identifiers(
        out,
        allowed_tables={"t_om_offline_device_list", "t_om_data_alarm_summary"},
    )
    assert ok2 is True, err2


def test_suggested_filters_rewrite_om_type() -> None:
    from app.nl2sql.suggested_filters_rewrite import rewrite_sql_with_suggested_filters

    sql = (
        "SELECT type, online_rate FROM om_result.t_om_device_online_by_type "
        "WHERE snapshot_time = (SELECT max(snapshot_time) FROM om_result.t_om_data_alarm_summary)"
    )
    filters = [
        {
            "table": "t_om_device_online_by_type",
            "column": "type",
            "op": "=",
            "value": "GNSS",
            "source": "om_device_type",
        }
    ]
    out, notes = rewrite_sql_with_suggested_filters(sql, filters)
    assert notes
    assert "type = 'GNSS'" in out or 'type = "GNSS"' in out or "type = 'GNSS'" in out.replace(
        '"', "'"
    )


def test_ops_safe_suggested_filters_keeps_only_om_type() -> None:
    from app.nl2sql.om_intent import ops_safe_suggested_filters

    filters = [
        {
            "column": "station_name",
            "op": "in",
            "value": ["F1-7", "F2-7"],
            "source": "fcb_layer0",
        },
        {
            "column": "type",
            "op": "=",
            "value": "GNSS",
            "source": "om_device_type",
        },
        {
            "column": "project_name",
            "op": "in",
            "value": ["F2(望京)"],
            "source": "device_station_map",
        },
    ]
    safe = ops_safe_suggested_filters(filters)
    assert len(safe) == 1
    assert safe[0]["source"] == "om_device_type"
    assert safe[0]["column"] == "type"


def test_sanitize_ops_sql_strips_settlement_station_name() -> None:
    from app.nl2sql.om_intent import sanitize_ops_sql_settlement_artifacts

    polluted = (
        "SELECT DISTINCT ON (station_name) type, total, online, offline, online_rate, snapshot_time "
        "FROM om_result.t_om_device_online_by_type "
        "WHERE snapshot_time = (SELECT max(snapshot_time) FROM om_result.t_om_device_online_by_type) "
        "AND station_name IN ('F8-10','F9-6','F2-7') "
        "ORDER BY station_name, data_time DESC"
    )
    out, notes = sanitize_ops_sql_settlement_artifacts(polluted)
    assert "station_name" not in out.lower()
    assert "distinct on" not in out.lower()
    assert "t_om_device_online_by_type" in out.lower()
    assert "snapshot_time" in out.lower()
    assert any("ops_strip" in n for n in notes)


def test_schema_linker_om_skips_fcb_station_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.core.config import get_app_config
    from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache
    from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
    from app.nl2sql.schema_linker import link_schema
    from app.nl2sql.schema_routing import clear_schema_routing_cache
    from app.nl2sql.semantic_layer import SemanticBinding

    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    clear_schema_routing_cache()

    table_columns = {
        "t_om_device_online_by_type": {
            "type",
            "total",
            "online",
            "offline",
            "online_rate",
            "snapshot_time",
        },
    }
    intent = QuestionIntent(
        raw_question="请帮我查询各类仪器最新的在线情况",
        scope_question="请帮我查询各类仪器最新的在线情况",
        time_window=None,
        scope=QuestionScopeIntent(),
    )
    linked = link_schema(
        "请帮我查询各类仪器最新的在线情况",
        intent,
        SemanticBinding(
            semantic_version="test",
            preferred_station_names=["F8-10", "F9-6", "F2-7"],
            project_names=["F2(望京)"],
            default_table="t_om_device_online_by_type",
        ),
        table_columns,
        allowlist={"t_om_device_online_by_type"},
    )
    assert linked.status != "failed", linked.fail_reason
    assert all(f.get("column") != "station_name" for f in linked.suggested_filters)
    assert all(f.get("source") != "fcb_layer0" for f in linked.suggested_filters)
    assert all(f.get("column") != "project_name" for f in linked.suggested_filters)


def test_schema_linker_keeps_om_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.core.config import get_app_config
    from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache
    from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
    from app.nl2sql.schema_linker import link_schema
    from app.nl2sql.schema_routing import clear_schema_routing_cache
    from app.nl2sql.semantic_layer import SemanticBinding

    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    clear_schema_routing_cache()

    table_columns = {
        "t_om_device_online_by_type": {"type", "online_rate", "snapshot_time", "total"},
        "t_data_wash_fcb": {"station_name", "total_settle", "data_time"},
    }
    intent = QuestionIntent(
        raw_question="各仪器在线率",
        scope_question="各仪器在线率",
        time_window=None,
        scope=QuestionScopeIntent(),
    )
    linked = link_schema(
        "各仪器在线率",
        intent,
        SemanticBinding(
            semantic_version="test",
            om_instrument_types=["GNSS"],
            default_table="t_om_device_online_by_type",
        ),
        table_columns,
        allowlist={"t_om_device_online_by_type"},
    )
    assert linked.status != "failed", linked.fail_reason
    assert any(t.name == "t_om_device_online_by_type" for t in linked.tables)
    assert any(
        f.get("column") == "type" and f.get("source") == "om_device_type"
        for f in linked.suggested_filters
    )
