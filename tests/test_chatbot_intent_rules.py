"""chatbot_intent_rules 规则层单测。"""

from app.llm.graphs.chatbot_intent_rules import classify_chatbot_intent


def test_conceptual_prefers_kb():
    label, reason, _c = classify_chatbot_intent(
        "过热爆管的常见原因有哪些？",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert label == "kb_qa"
    assert "conceptual" in reason or reason == "default_kb_qa"


def test_data_query_ledger():
    label, reason, _c = classify_chatbot_intent(
        "查询台账里1号炉最近一次检修记录",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert label == "data_query"
    assert "structured" in reason


def test_images_force_kb():
    label, reason, _c = classify_chatbot_intent(
        "统计缺陷数量",
        enable_nl2sql_route=True,
        image_urls=["http://example.com/x.jpg"],
    )
    assert label == "kb_qa"
    assert "images" in reason


def test_nl2sql_disabled():
    label, _r, _c = classify_chatbot_intent(
        "列出本月缺陷单",
        enable_nl2sql_route=False,
        image_urls=[],
    )
    assert label == "kb_qa"
