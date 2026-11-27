"""data_query 通俗「数据来源」板块单测。"""

from __future__ import annotations

from types import SimpleNamespace

from app.llm.graphs.chatbot_nl2sql_data_source import (
    Nl2sqlDataSourceFacts,
    append_data_source_section,
    build_data_source_section,
    data_source_facts_from_prepare,
)


def test_build_section_period_station():
    facts = Nl2sqlDataSourceFacts(
        library_id="fcb",
        library_display_name="分层标",
        library_table="t_data_wash_fcb",
        library_source="parsed",
        district="朝阳区",
        station_name="F8(周村)",
        grain="station",
        time_start="2026-10-01 00:00:00",
        time_end="2026-10-31 23:59:59",
        period_deterministic="hit",
        period_eligible=True,
        sql=(
            "WITH ranked AS (SELECT * FROM t_data_wash_fcb f "
            "JOIN t_station s ON f.project_name = s.name WHERE s.area = '朝阳区') "
            "SELECT * FROM ranked"
        ),
    )
    text = build_data_source_section(facts)
    assert text.startswith("**数据来源**")
    assert "分层标监测数据" in text
    assert "站点行政区对照" in text
    assert "朝阳区" in text
    assert "F8(周村)" in text
    assert "2026-10-01" in text and "2026-10-31" in text
    assert "窗末" in text or "周期" in text
    assert "层位 0" in text or "层位0" in text
    assert "t_data_wash_fcb" not in text
    assert "SELECT" not in text


def test_build_section_default_library():
    facts = Nl2sqlDataSourceFacts(
        library_id="fcb",
        library_display_name="分层标",
        library_table="t_data_wash_fcb",
        library_source="default",
        grain="station",
        time_is_latest=True,
        sql="SELECT * FROM t_data_wash_fcb",
    )
    text = build_data_source_section(facts)
    assert "默认库" in text
    assert "最新一条" in text


def test_build_section_ops_from_sql():
    facts = Nl2sqlDataSourceFacts(
        ops_routed=True,
        period_deterministic="skipped_ops",
        sql=(
            "SELECT type, online_rate FROM om_result.t_om_device_online_by_type "
            "WHERE snapshot_time = (SELECT max(snapshot_time) FROM om_result.t_om_device_online_by_type)"
        ),
    )
    text = build_data_source_section(facts)
    assert "运维监测" in text
    assert "仪器在线率" in text
    assert "最新一批" in text
    assert "不涉及沉降" in text
    assert "t_om_device_online_by_type" not in text


def test_build_section_skips_cte_names():
    facts = Nl2sqlDataSourceFacts(
        library_display_name="基岩标",
        library_table="t_data_wash_jyb",
        library_source="parsed",
        grain="district",
        time_start="2026-01-01",
        time_end="2026-01-31",
        period_eligible=True,
        sql=(
            "WITH period AS (SELECT 1), ranked AS (SELECT 1) "
            "SELECT area FROM t_data_wash_jyb"
        ),
    )
    text = build_data_source_section(facts)
    assert "基岩标监测数据" in text
    # CTE 名不得当数据表展示
    table_line = next((ln for ln in text.splitlines() if "数据表" in ln), "")
    assert "ranked" not in table_line.lower()
    assert "period AS" not in table_line.lower()
    assert "按行政区" in text


def test_append_idempotent():
    facts = Nl2sqlDataSourceFacts(
        library_display_name="分层标",
        library_table="t_data_wash_fcb",
        library_source="parsed",
        sql="SELECT 1 FROM t_data_wash_fcb",
    )
    once = append_data_source_section("概括正文", facts)
    twice = append_data_source_section(once, facts)
    assert once.count("**数据来源**") == 1
    assert twice.count("**数据来源**") == 1


def test_empty_facts_no_section():
    assert build_data_source_section(None) == ""
    assert build_data_source_section(Nl2sqlDataSourceFacts()) == ""


def test_facts_from_prepare(monkeypatch):
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    from app.llm.graphs.chatbot_business_profile import clear_chatbot_business_profile_cache

    clear_chatbot_business_profile_cache()
    prepare = SimpleNamespace(
        ops_routed=False,
        library_source="parsed",
        library_id="fcb",
        period_eligible=True,
        original_query="朝阳区分层标近期沉降",
        confirmed_scope={"district": "朝阳区", "station_id": None, "station_name": None},
        library=SimpleNamespace(id="fcb", display_name="分层标", table="t_data_wash_fcb"),
        scope=SimpleNamespace(
            grain="station",
            time_snapshot={
                "time_window_tag": "recent_7_days",
                "statistical_time_range": {
                    "start": "2026-11-01 00:00:00",
                    "end": "2026-11-07 23:59:59",
                },
            },
            annual_window=None,
        ),
    )
    facts = data_source_facts_from_prepare(
        prepare,
        sql="SELECT * FROM t_data_wash_fcb",
        period_deterministic="hit",
        user_query="朝阳区分层标近期沉降",
    )
    assert facts.library_display_name == "分层标"
    assert facts.district == "朝阳区"
    assert facts.period_deterministic == "hit"
    text = build_data_source_section(facts)
    assert "朝阳区" in text
    assert "分层标" in text


def test_finalize_appends_data_source():
    from app.llm.graphs.chatbot_nl2sql_answer import (
        Nl2sqlAnalysisStreamPlan,
        finalize_streamed_nl2sql_analysis,
    )

    facts = Nl2sqlDataSourceFacts(
        library_display_name="分层标",
        library_table="t_data_wash_fcb",
        library_source="parsed",
        district="海淀区",
        grain="station",
        time_is_latest=True,
        sql="SELECT * FROM t_data_wash_fcb",
    )
    plan = Nl2sqlAnalysisStreamPlan(
        system="sys",
        user_content="user",
        table_fallback="| a |\n|---|\n| 1 |",
        display_rows=[{"a": 1}],
        total_row_count=1,
        analysis_prompt_row_count=1,
        sql=facts.sql,
        user_query="海淀区最新沉降",
        data_source_facts=facts.to_dict(),
    )
    result = finalize_streamed_nl2sql_analysis(plan, "本轮查询到海淀区最新沉降情况。")
    assert "**数据来源**" in result.answer_text
    assert "海淀区" in result.answer_text
    assert result.answer_text.index("本轮查询") < result.answer_text.index("**数据来源**")
