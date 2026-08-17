"""chatbot_intent_rules 规则层单测。"""

from app.llm.graphs.chatbot_intent_rules import (
    build_intent_context_from_history,
    classify_chatbot_intent_by_rules,
)
from app.services.chatbot_image_utils import PROCESSED_IMAGE_BLOCK_MARKER


def test_conceptual_prefers_kb():
    r = classify_chatbot_intent_by_rules(
        "过热爆管的常见原因有哪些？",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "kb_qa"
    assert "conceptual" in r.intent_reason or r.intent_reason == "default_kb_qa"


def test_data_query_ledger():
    r = classify_chatbot_intent_by_rules(
        "查询台账里1号炉最近一次检修记录",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "data_query"
    assert "structured" in r.intent_reason


def test_how_much_with_ops_context_is_data_query():
    """「是多少」须同时命中负荷/当前/锅炉等正配语境才走查数。"""
    r = classify_chatbot_intent_by_rules(
        "本厂1号锅炉当前负荷是多少",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "data_query"
    assert r.intent_reason == "structured_query_heuristic"
    assert r.intent_confidence >= 0.8


def test_how_much_without_ops_context_stays_kb():
    """无工况语境的「是多少」仍默认知识问答，降低规范/建议值误入 NL2SQL。"""
    for q in (
        "蠕变寿命一般是多少",
        "建议值是多少",
        "标准规定允许偏差是多少",
    ):
        r = classify_chatbot_intent_by_rules(q, enable_nl2sql_route=True, image_urls=[])
        assert r.intent_label == "kb_qa", q
        assert r.intent_reason in {"default_kb_qa", "conceptual_qa_heuristic"}, (q, r.intent_reason)


def test_mixed_hybrid():
    """同时命中查数 + 概念/机理 → hybrid_qa，不再二选一。"""
    r = classify_chatbot_intent_by_rules(
        "查出超温点列表并结合规程说明如何处置",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "hybrid_qa"
    assert r.intent_reason == "mixed_hybrid"
    assert r.intent_confidence >= 0.7


def test_mixed_hybrid_query_plus_plan_deliverable():
    """查数 + 出具/方案类通用诉求 → hybrid_qa（如「查询…并出具一份…计划」）。"""
    r = classify_chatbot_intent_by_rules(
        "请查询超温数据，并帮我出具一份检修计划",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "hybrid_qa"
    assert r.intent_reason == "mixed_hybrid"


def test_plan_deliverable_alone_stays_kb():
    """仅方案/出具类、无查数标记时仍走知识问答。"""
    r = classify_chatbot_intent_by_rules(
        "帮我出具一份处置方案",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "kb_qa"
    assert r.intent_reason == "conceptual_qa_heuristic"


def test_images_force_kb():
    r = classify_chatbot_intent_by_rules(
        "统计缺陷数量",
        enable_nl2sql_route=True,
        image_urls=["http://example.com/x.jpg"],
    )
    assert r.intent_label == "kb_qa"
    assert "images" in r.intent_reason


def test_nl2sql_disabled():
    r = classify_chatbot_intent_by_rules(
        "列出本月缺陷单",
        enable_nl2sql_route=False,
        image_urls=[],
    )
    assert r.intent_label == "kb_qa"


def test_short_followup_continues_kb_with_multimodal_history():
    hist = [
        {
            "role": "user",
            "content": "这张图是什么缺陷"
            + PROCESSED_IMAGE_BLOCK_MARKER
            + "\n- http://example.com/a.jpg",
        },
        {"role": "assistant", "content": "从图像特征看可能是疲劳裂纹，建议结合运行记录复核。"},
    ]
    r = classify_chatbot_intent_by_rules(
        "你呢",
        enable_nl2sql_route=True,
        image_urls=[],
        history_messages=hist,
    )
    assert r.intent_label == "kb_qa"
    assert "short_followup" in r.intent_reason
    assert r.prev_task_type == "multimodal_qa"


def test_short_query_cold_start_still_clarify():
    r = classify_chatbot_intent_by_rules(
        "呢呢",
        enable_nl2sql_route=True,
        image_urls=[],
        history_messages=None,
    )
    assert r.intent_label == "clarify"


def test_build_intent_context_prev_task_after_clarify():
    hist = [
        {"role": "user", "content": "帮我看看"},
        {"role": "assistant", "content": "为了更准确地回答你，请补充更具体的信息：你要咨询的是哪一项业务"},
    ]
    summary, prev = build_intent_context_from_history(hist)
    assert prev == "after_clarify"
    assert "assistant" in summary
