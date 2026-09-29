"""「最新」每站模板 + suggested_filters Prompt 约束单测。"""

from __future__ import annotations

import re

import pytest

from app.nl2sql.latest_per_station import (
    LATEST_PER_STATION_TAG,
    resolve_latest_grain,
    rewrite_sql_for_latest_per_station,
    strip_calendar_data_time_predicates,
)
from app.nl2sql.question_intent import resolve_question_intent
from app.nl2sql.question_intent_display import format_parsed_intent_prompt_block
from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
from app.nl2sql.time_intent_display import (
    extract_time_window_from_question,
    extract_time_window_tag,
    resolve_statistical_time_range_display,
)


def test_extract_latest_per_station_tag() -> None:
    win = extract_time_window_from_question("请帮我查询全市最新的地面沉降数据")
    assert win is not None
    assert win[2] == LATEST_PER_STATION_TAG
    assert extract_time_window_tag("朝阳区各站最新沉降") == LATEST_PER_STATION_TAG
    # 显式昨天优先于「最新」修饰（昨天规则在前）
    assert extract_time_window_tag("昨天最新沉降") == "yesterday"


def test_latest_display_not_yesterday() -> None:
    start, end = resolve_statistical_time_range_display("全市最新地面沉降")
    assert start == "每站最新一条"
    assert end == "每站最新一条"


def test_prompt_forbids_in_select_and_emits_latest_template() -> None:
    q = "请帮我查询全市最新的地面沉降数据"
    intent = QuestionIntent(
        raw_question=q,
        scope_question=q,
        time_window=extract_time_window_from_question(q),
        scope=QuestionScopeIntent(device_type="fcb"),
    )
    linked = {
        "status": "ok",
        "tables": [{"name": "t_data_wash_fcb"}],
        "suggested_filters": [
            {
                "table": "t_data_wash_fcb",
                "column": "station_name",
                "op": "in",
                "value": ["F1-7", "F2-7"],
                "source": "fcb_layer0",
            }
        ],
    }
    semantic = {
        "version": "test",
        "dimensions": {
            "preferred_station_names": ["F1-7", "F2-7"],
            "device_coverage_project_names": ["F1(王四营)", "F2(望京)"],
        },
        "warnings": ["fcb_layer0_city_filter"],
    }
    block = format_parsed_intent_prompt_block(intent, semantic=semantic, linked_schema=linked)
    assert "禁止写 IN (SELECT" in block
    assert "可省略" in block
    assert LATEST_PER_STATION_TAG in block
    assert "DISTINCT ON" in block
    assert "MAX(data_time)" in block
    assert "勿写日历日窗" in block


def test_rewrite_injects_distinct_on_and_strips_calendar() -> None:
    sql = (
        "SELECT f.station_name, f.total_settle, f.data_time "
        "FROM t_data_wash_fcb f "
        "WHERE f.data_time >= (CURRENT_DATE - INTERVAL '1 day') "
        "AND f.data_time < CURRENT_DATE "
        "ORDER BY f.total_settle DESC LIMIT 50"
    )
    out, notes = rewrite_sql_for_latest_per_station(sql, grain="station_name", prefer_distinct_on=True)
    assert re.search(r"(?i)DISTINCT\s+ON\s*\(\s*station_name\s*\)", out)
    assert "data_time DESC" in out
    assert "CURRENT_DATE" not in out
    assert any(n.startswith("latest_per_station_inject_distinct_on") for n in notes)
    assert "latest_per_station_strip_calendar" in notes


def test_rewrite_keeps_existing_max_pattern() -> None:
    sql = (
        "SELECT f.station_name, f.data_time, f.total_settle FROM t_data_wash_fcb f "
        "INNER JOIN ("
        "  SELECT station_name, MAX(data_time) AS max_dt FROM t_data_wash_fcb GROUP BY station_name"
        ") t ON f.station_name = t.station_name AND f.data_time = t.max_dt"
    )
    out, notes = rewrite_sql_for_latest_per_station(sql, grain="station_name")
    assert out == sql or "MAX(data_time)" in out
    assert "latest_per_station_max_present" in notes


def test_strip_calendar_predicates() -> None:
    sql = (
        "SELECT * FROM t_data_wash_fcb "
        "WHERE data_time >= '2026-09-28' AND data_time < '2026-09-29' AND station_name = 'F1-7'"
    )
    out, changed = strip_calendar_data_time_predicates(sql)
    assert changed
    assert "data_time" not in out.lower() or "MAX" in out.upper()
    assert "station_name = 'F1-7'" in out
    assert "2026-09-28" not in out


def test_resolve_latest_grain_prefers_station_name() -> None:
    grain = resolve_latest_grain(
        [
            {"column": "project_name", "op": "in", "value": ["F1"], "source": "device_station_map"},
            {"column": "station_name", "op": "in", "value": ["F1-7"], "source": "fcb_layer0"},
        ]
    )
    assert grain == "station_name"


def test_resolve_question_intent_latest_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache

    clear_nl2sql_business_profile_cache()
    try:
        intent = resolve_question_intent("请帮我查询全市最新的地面沉降数据")
        assert intent.time_window_tag == LATEST_PER_STATION_TAG
    finally:
        clear_nl2sql_business_profile_cache()


def test_chain_rewrite_skips_yesterday_for_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("NL2SQL_SQL_DIALECT", "postgres")
    from app.nl2sql.chain import NL2SQLChain
    from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache

    clear_nl2sql_business_profile_cache()
    try:
        chain = NL2SQLChain.__new__(NL2SQLChain)
        sql = (
            "SELECT station_name, total_settle, data_time FROM t_data_wash_fcb "
            "WHERE data_time >= '2026-09-28' ORDER BY total_settle DESC LIMIT 20"
        )
        parsed = {
            "time_window_tag": LATEST_PER_STATION_TAG,
            "linked_schema": {
                "suggested_filters": [
                    {
                        "table": "t_data_wash_fcb",
                        "column": "station_name",
                        "op": "in",
                        "value": ["F1-7", "F2-7"],
                        "source": "fcb_layer0",
                    }
                ]
            },
        }
        out, notes = chain._rewrite_query_filters(
            sql,
            question="全市最新地面沉降",
            time_intent_source="全市最新地面沉降",
            parsed_intent=parsed,
        )
        assert "DISTINCT ON (station_name)" in out
        assert "2026-09-28" not in out
        assert any("latest_per_station" in n for n in notes)
        assert not any("yesterday" in n for n in notes)
    finally:
        clear_nl2sql_business_profile_cache()
