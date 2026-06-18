"""plan「锚点向前 N 天」+ 用户问句锚点 → SQL 时间窗合成。"""

from __future__ import annotations

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.question_intent import resolve_question_intent
from app.nl2sql.question_intent_display import question_intent_to_dict
from app.nl2sql.time_intent_display import (
    build_anchor_lookback_time_window,
    extract_time_anchor_from_question,
    parse_plan_anchor_lookback_days,
)


def test_parse_plan_anchor_lookback_days() -> None:
    assert parse_plan_anchor_lookback_days("在事故锚点向前 3 天时间窗内查询") == 3
    assert parse_plan_anchor_lookback_days("事故锚点向前3天内") == 3
    assert parse_plan_anchor_lookback_days("事故锚点向前三天内") == 3
    assert parse_plan_anchor_lookback_days("查询近3次壁厚") is None


def test_extract_time_anchor_day_before_yesterday() -> None:
    anchor = extract_time_anchor_from_question("3号炉水冷壁前天发生泄爆")
    assert anchor is not None
    end_expr, tag = anchor
    assert end_expr == "DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
    assert tag == "anchor_day_before_yesterday"


def test_extract_time_anchor_skips_rolling_window() -> None:
    assert extract_time_anchor_from_question("近半年超温统计") is None


def test_build_anchor_lookback_time_window() -> None:
    start, end, tag = build_anchor_lookback_time_window(
        "DATE_SUB(CURDATE(), INTERVAL 1 DAY)", 3
    )
    assert start == "DATE_SUB(DATE_SUB(CURDATE(), INTERVAL 1 DAY), INTERVAL 3 DAY)"
    assert end == "DATE_SUB(CURDATE(), INTERVAL 1 DAY)"
    assert tag == "anchor_lookback_3d"


def test_rewrite_query_filters_anchor_lookback_from_plan_and_user() -> None:
    chain = NL2SQLChain.__new__(NL2SQLChain)
    user_q = "3号锅炉水冷壁前天发生泄爆"
    intent = resolve_question_intent(user_q, time_intent_source=user_q)
    parsed = question_intent_to_dict(intent)
    plan_q = f"{user_q}。在事故锚点向前3天时间窗内查询壁温超温数据"

    sql = (
        "SELECT 1 FROM monitor_hotarea_temp "
        "WHERE start_time >= @t_start AND start_time < @t_end"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql,
        question=plan_q,
        time_intent_source=user_q,
        parsed_intent=parsed,
    )
    assert "DATE_SUB(" in rewritten
    assert "@t_start" not in rewritten
    assert "@t_end" not in rewritten
    assert any("time_placeholder_t_start" in n for n in notes)
    assert intent.time_window_tag == "day_before_yesterday"
    assert intent.time_anchor is not None


def test_rewrite_query_filters_no_plan_trigger_keeps_original_window() -> None:
    chain = NL2SQLChain.__new__(NL2SQLChain)
    user_q = "请分析1号锅炉前天的超温"
    intent = resolve_question_intent(user_q, time_intent_source=user_q)
    parsed = question_intent_to_dict(intent)
    plan_q = f"{user_q}。查询对应管段累计超温时长"

    sql = "SELECT 1 WHERE start_time >= @t_start AND start_time < @t_end"
    rewritten, _notes = chain._rewrite_query_filters(
        sql,
        question=plan_q,
        time_intent_source=user_q,
        parsed_intent=parsed,
    )
    assert "DATE_SUB(CURDATE(), INTERVAL 2 DAY)" in rewritten
    assert "DATE_SUB(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert "anchor_lookback" not in rewritten


def test_rewrite_query_filters_plan_trigger_without_anchor_leakage_fallback_now() -> None:
    chain = NL2SQLChain.__new__(NL2SQLChain)
    user_q = "3号锅炉水冷壁泄爆分析"
    intent = resolve_question_intent(user_q, time_intent_source=user_q)
    parsed = question_intent_to_dict(intent)
    assert parsed.get("time_anchor") is None
    plan_q = f"{user_q}。在事故锚点向前3天时间窗内查询"
    rewrite_meta: dict = {}

    sql = "SELECT 1 WHERE start_time >= @t_start AND start_time < @t_end"
    rewritten, _notes = chain._rewrite_query_filters(
        sql,
        question=plan_q,
        time_intent_source=user_q,
        parsed_intent=parsed,
        analysis_type="img_diag_leakage_burst",
        rewrite_meta=rewrite_meta,
    )
    assert "@t_start" not in rewritten
    assert "@t_end" not in rewritten
    assert "NOW()" in rewritten
    assert rewrite_meta.get("time_rewrite_warnings") == ["anchor_fallback_now"]
    assert rewrite_meta.get("effective_time_window", {}).get("tag", "").endswith("fallback_now")


def test_rewrite_query_filters_plan_trigger_without_anchor_defect_fallback_now() -> None:
    chain = NL2SQLChain.__new__(NL2SQLChain)
    user_q = "3号锅炉水冷壁缺陷识别"
    intent = resolve_question_intent(user_q, time_intent_source=user_q)
    parsed = question_intent_to_dict(intent)
    assert parsed.get("time_anchor") is None
    rewrite_meta: dict = {}
    plan_q = f"{user_q}。在事故锚点向前3天时间窗内查询"

    sql = "SELECT 1 WHERE start_time >= @t_start AND start_time < @t_end"
    rewritten, _notes = chain._rewrite_query_filters(
        sql,
        question=plan_q,
        time_intent_source=user_q,
        parsed_intent=parsed,
        analysis_type="img_diag_defect_ident",
        rewrite_meta=rewrite_meta,
    )
    assert "@t_start" not in rewritten
    assert "@t_end" not in rewritten
    assert "NOW()" in rewritten
    assert rewrite_meta.get("time_rewrite_warnings") == ["anchor_fallback_now"]
    assert rewrite_meta.get("effective_time_window", {}).get("tag", "").endswith("fallback_now")


def test_validate_unresolved_time_placeholders_rejects() -> None:
    chain = NL2SQLChain.__new__(NL2SQLChain)
    ok, reason = chain._validate_unresolved_time_placeholders(
        "SELECT 1 WHERE t >= @t_start AND t < @t_end"
    )
    assert ok is False
    assert reason is not None
    assert "unresolved time placeholders" in reason.lower()


def test_collect_nl2sql_time_intent_warnings() -> None:
    from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
    from app.models.analysis import AnalysisNL2SQLCall

    calls = [
        AnalysisNL2SQLCall(
            item_id="q1",
            purpose="p",
            question="q",
            status="success",
            question_intent={"time_rewrite_warnings": ["anchor_fallback_now"]},
        ),
        AnalysisNL2SQLCall(
            item_id="q2",
            purpose="p",
            question="q",
            status="failed",
            error="unresolved time placeholders (@t_start/@t_end)",
        ),
    ]
    warnings = AnalysisGraphRunner._collect_nl2sql_time_intent_warnings(calls)
    assert len(warnings) == 2
    assert any("NOW()" in w for w in warnings)
    assert any("占位符" in w for w in warnings)
