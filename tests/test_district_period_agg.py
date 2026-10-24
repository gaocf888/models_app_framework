"""区均周期沉降：grain / GroupingError refine 指引 / RAG 注入文案。"""

from __future__ import annotations


def test_is_district_grain_and_grouping_error() -> None:
    from app.nl2sql.district_period_agg import (
        is_district_grain_question,
        is_grouping_executor_error,
        needs_district_period_refine,
    )

    q = "哪些行政区近期沉降相对偏大？"
    assert is_district_grain_question(q)
    assert not is_district_grain_question("F2本月沉降多少")

    err = (
        'GroupingError: column "t_data_wash_fcb.data_time" '
        "must appear in the GROUP BY clause or be used in an aggregate function"
    )
    assert is_grouping_executor_error(err)
    assert needs_district_period_refine(q, "SELECT 1", err)

    bad_sql = (
        "SELECT ... ROW_NUMBER() OVER (...) ... "
        "MIN(CASE WHEN data_time = settle_start_time THEN total_settle END)"
    )
    assert needs_district_period_refine("随便问一句", bad_sql, err)


def test_district_prompt_block_has_cte_bans() -> None:
    from app.nl2sql.district_period_agg import district_period_prompt_block

    block = district_period_prompt_block(include_skeleton=True)
    assert "ROW_NUMBER" in block
    assert "GROUP BY area" in block or "GROUP BY s.area" in block or "GROUP BY area" in block
    assert "settle_start_time" in block  # 出现在禁止项
    assert "AVG(period_settle_mm)" in block or "avg_period_settle_mm" in block
    assert "WITH ranked" in block


def test_chatbot_district_hint_includes_skeleton() -> None:
    from app.llm.graphs.chatbot_nl2sql_answer import _chatbot_sql_gen_extra_hint

    hint = _chatbot_sql_gen_extra_hint(
        "哪些行政区近期沉降相对偏大？", grain="district"
    )
    assert "WITH ranked" in hint
    assert "禁止" in hint or "强制" in hint
    assert "DISTINCT ON" not in hint


def test_refine_system_gets_district_guidance_flag() -> None:
    """needs_district_period_refine 为真时，提示块含 refine 专用句。"""
    from app.nl2sql.district_period_agg import (
        DISTRICT_PERIOD_AGG_REFINE_EXTRA,
        needs_district_period_refine,
    )

    assert "禁止把报错列简单塞进 GROUP BY" in DISTRICT_PERIOD_AGG_REFINE_EXTRA
    assert needs_district_period_refine(
        "哪些行政区近期沉降相对偏大？",
        "SELECT x FROM t GROUP BY y",
        "must appear in the GROUP BY clause",
    )
