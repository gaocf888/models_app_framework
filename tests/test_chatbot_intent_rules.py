"""chatbot_intent_rules 规则层单测。"""

from app.llm.graphs.chatbot_intent_rules import (
    build_intent_context_from_history,
    classify_chatbot_intent,
)
from app.services.chatbot_image_utils import PROCESSED_IMAGE_BLOCK_MARKER


def test_conceptual_prefers_kb():
    r = classify_chatbot_intent(
        "过热爆管的常见原因有哪些？",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "kb_qa"
    assert "conceptual" in r.intent_reason or r.intent_reason == "default_kb_qa"


def test_data_query_ledger():
    r = classify_chatbot_intent(
        "查询台账里1号炉最近一次检修记录",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "data_query"
    assert "structured" in r.intent_reason


def test_images_force_kb():
    r = classify_chatbot_intent(
        "统计缺陷数量",
        enable_nl2sql_route=True,
        image_urls=["http://example.com/x.jpg"],
    )
    assert r.intent_label == "kb_qa"
    assert "images" in r.intent_reason


def test_nl2sql_disabled():
    r = classify_chatbot_intent(
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
    r = classify_chatbot_intent(
        "你呢",
        enable_nl2sql_route=True,
        image_urls=[],
        history_messages=hist,
    )
    assert r.intent_label == "kb_qa"
    assert "short_followup" in r.intent_reason
    assert r.prev_task_type == "multimodal_qa"


def test_short_query_cold_start_still_clarify():
    r = classify_chatbot_intent(
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
