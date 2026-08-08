"""chatbot_intent 后端切换与 BERT 回退单测。"""

from unittest.mock import patch

from app.llm.graphs.chatbot_intent import classify_chatbot_intent, resolve_intent_backend
from app.llm.graphs.chatbot_intent_bert import ChatbotIntentBertClassifier


def test_resolve_intent_backend_defaults_rules():
    with patch("app.llm.graphs.chatbot_intent.get_app_config") as mock_cfg:
        mock_cfg.return_value.chatbot.intent_backend = "rules"
        assert resolve_intent_backend() == "rules"


def test_resolve_intent_backend_llm_valid():
    with patch("app.llm.graphs.chatbot_intent.get_app_config") as mock_cfg:
        mock_cfg.return_value.chatbot.intent_backend = "llm"
        assert resolve_intent_backend() == "llm"


def test_resolve_intent_backend_invalid_falls_back_rules():
    with patch("app.llm.graphs.chatbot_intent.get_app_config") as mock_cfg:
        mock_cfg.return_value.chatbot.intent_backend = "unknown"
        assert resolve_intent_backend() == "rules"


def test_facade_rules_backend_matches_rules_module():
    r = classify_chatbot_intent(
        "查询台账里1号炉最近一次检修记录",
        enable_nl2sql_route=True,
        image_urls=[],
        backend="rules",
    )
    assert r.intent_label == "data_query"
    assert "structured" in r.intent_reason


def test_sync_classify_llm_backend_falls_back_rules():
    r = classify_chatbot_intent(
        "查台账记录并解释过热原因",
        enable_nl2sql_route=True,
        image_urls=[],
        backend="llm",
    )
    # sync 路径不调用 Ollama，回退 rules（混合句 → hybrid_qa）
    assert r.intent_label in {"kb_qa", "data_query", "hybrid_qa"}


def test_facade_bert_hard_gate_images_still_kb():
    r = classify_chatbot_intent(
        "统计缺陷数量",
        enable_nl2sql_route=True,
        image_urls=["http://example.com/x.jpg"],
        backend="bert",
    )
    assert r.intent_label == "kb_qa"
    assert "images" in r.intent_reason


def test_bert_fallback_to_rules_when_model_missing(monkeypatch):
    ChatbotIntentBertClassifier.reset_instance_for_tests()
    monkeypatch.delenv("CHATBOT_INTENT_BERT_MODEL_PATH", raising=False)
    monkeypatch.delenv("CHATBOT_INTENT_BERT_MODEL_NAME", raising=False)

    with patch("app.llm.graphs.chatbot_intent_bert.get_app_config") as mock_cfg:
        cfg = mock_cfg.return_value.chatbot
        cfg.intent_bert_model_path = None
        cfg.intent_bert_model_name = None
        cfg.intent_bert_fallback_to_rules = True
        cfg.intent_bert_device = "cpu"
        cfg.intent_bert_max_length = 256

        r = classify_chatbot_intent(
            "过热爆管的常见原因有哪些？",
            enable_nl2sql_route=True,
            image_urls=[],
            backend="bert",
        )
    assert r.intent_label == "kb_qa"
    assert "bert_fallback_rules" in r.intent_reason


def test_bert_predict_label_mocked():
    ChatbotIntentBertClassifier.reset_instance_for_tests()
    clf = ChatbotIntentBertClassifier()
    clf._model = object()
    clf._tokenizer = object()
    clf._id2label = {0: "data_query", 1: "kb_qa", 2: "clarify"}

    with patch.object(ChatbotIntentBertClassifier, "_ensure_loaded", return_value=True):
        with patch.object(ChatbotIntentBertClassifier, "_predict_label", return_value=("data_query", 0.91)):
            label, conf = clf.classify("列出本月缺陷单", history_summary="", prev_task_type="unknown")
    assert label == "data_query"
    assert conf == 0.91
