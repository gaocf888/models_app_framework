"""L1 时间骨架：意图归一、抽取、渲染。"""

from __future__ import annotations

from app.nl2sql.sql_cache import build_nl2sql_sql_cache_key
from app.nl2sql.sql_skeleton import (
    build_nl2sql_l1_cache_key,
    extract_time_skeleton_from_sql,
    normalize_nl2sql_question_intent,
    render_sql_time_skeleton,
    resolve_relative_day_offset,
    resolve_time_intent,
    skeleton_payload_to_json,
    skeleton_payload_from_json,
)


def test_normalize_intent_collapses_relative_days() -> None:
    a = normalize_nl2sql_question_intent("请分析前天的超温。查询参数")
    b = normalize_nl2sql_question_intent("请分析昨天的超温。查询参数")
    assert a == b
    assert "<R>" in a


def test_l1_key_same_for_different_relative_words() -> None:
    k1 = build_nl2sql_l1_cache_key(
        data_source_fp="x" * 32,
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        question="用户。查询A",
        schema_fp="s" * 32,
        policy_fp="p" * 32,
    )
    k2 = build_nl2sql_l1_cache_key(
        data_source_fp="x" * 32,
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        question="用户。查询B",
        schema_fp="s" * 32,
        policy_fp="p" * 32,
    )
    assert k1 != k2
    k_y = build_nl2sql_l1_cache_key(
        data_source_fp="x" * 32,
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        question="请分析前天的超温。查询",
        schema_fp="s" * 32,
        policy_fp="p" * 32,
    )
    k_q = build_nl2sql_l1_cache_key(
        data_source_fp="x" * 32,
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        question="请分析昨天的超温。查询",
        schema_fp="s" * 32,
        policy_fp="p" * 32,
    )
    assert k_y == k_q


def test_resolve_relative_day_offset() -> None:
    assert resolve_relative_day_offset("昨天的情况") == 1
    assert resolve_relative_day_offset("前天") == 2
    assert resolve_relative_day_offset("大前天") == 3
    assert resolve_relative_day_offset("今天") == 0


def test_extract_and_render_date_sub_roundtrip() -> None:
    sql = (
        "SELECT * FROM t WHERE d >= DATE_SUB(CURDATE(), INTERVAL 2 DAY) "
        "AND d < DATE_SUB(CURDATE(), INTERVAL 2 DAY)"
    )
    payload = extract_time_skeleton_from_sql(sql)
    assert payload is not None
    raw = skeleton_payload_to_json(payload)
    back = skeleton_payload_from_json(raw)
    assert back == payload
    out = render_sql_time_skeleton(back, "我想看昨天的数据")
    assert out is not None
    assert "INTERVAL 1 DAY" in out
    assert "INTERVAL 2 DAY" not in out


def test_extract_rejects_unknown_wide_range() -> None:
    """跨度超过 62 天且无法分类为周/月/滚动的区间不写 L1。"""
    sql = "SELECT * FROM t WHERE d BETWEEN '2026-01-01' AND '2026-06-01'"
    assert extract_time_skeleton_from_sql(sql) is None


def test_intent_normalize_iso_week_and_rolling() -> None:
    w1 = normalize_nl2sql_question_intent("分析本周超温情况")
    w2 = normalize_nl2sql_question_intent("分析上周超温情况")
    assert w1 == w2 and "<ISO_WEEK>" in w1
    assert "<ROLLING_N:7>" in normalize_nl2sql_question_intent("统计近7天数据")
    assert normalize_nl2sql_question_intent("近 10 天趋势") != normalize_nl2sql_question_intent("近 7 天趋势")
    assert normalize_nl2sql_question_intent("上个月收入") == normalize_nl2sql_question_intent("上月收入")


def test_resolve_time_intent_priority() -> None:
    r = resolve_time_intent("本周近7天")  # 歧义：近 N 天优先
    assert r is not None and r.mode == "rolling" and r.rolling_n == 7


def test_extract_quoted_same_day() -> None:
    sql = (
        "SELECT * FROM t WHERE ts BETWEEN '2026-05-05 00:00:00' AND '2026-05-05 23:59:59'"
    )
    payload = extract_time_skeleton_from_sql(sql)
    assert payload is not None
    out = render_sql_time_skeleton(payload, "前天")
    assert out is not None
    assert "00:00:00" in out and "23:59:59" in out


def test_l2_cache_key_ignores_plan_context_rag_suffix() -> None:
    kw = dict(
        data_source_fp="d" * 32,
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        schema_fp="s" * 32,
        policy_fp="p" * 32,
    )
    core = "分析前天超温。查询超温事件明细。若用户未指定机组/区域，则不要在 WHERE 中臆造具体锅炉名或墙别。"
    k_a = build_nl2sql_sql_cache_key(**kw, question=f"{core}。请结合以下规则线索：片段A")
    k_b = build_nl2sql_sql_cache_key(**kw, question=f"{core}。请结合以下规则线索：完全不同的片段B")
    k_plain = build_nl2sql_sql_cache_key(**kw, question=core)
    assert k_a == k_b == k_plain


def test_l1_cache_key_ignores_plan_context_rag_suffix() -> None:
    kw = dict(
        data_source_fp="d" * 32,
        analysis_type="overheat_guidance",
        plan_item_id="q2",
        schema_fp="s" * 32,
        policy_fp="p" * 32,
    )
    core = "分析前天超温。查询运行参数"
    k_a = build_nl2sql_l1_cache_key(**kw, question=f"{core}。请结合以下规则线索：X")
    k_b = build_nl2sql_l1_cache_key(**kw, question=f"{core}。请结合以下规则线索：Y")
    assert k_a == k_b
