"""chatbot_intent_llm 模式 B 与窄触发单测。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.graphs.chatbot_intent_llm import (
    classify_chatbot_intent_by_llm,
    should_invoke_intent_llm,
)
from app.llm.graphs.chatbot_intent_rules import IntentRuleResult


def test_should_invoke_low_confidence():
    r = IntentRuleResult("kb_qa", "mixed_prefers_conceptual", 0.72, "", "text_kb_qa")
    assert should_invoke_intent_llm(r, conf_threshold=0.78) is True


def test_should_not_invoke_high_confidence_structured():
    r = IntentRuleResult("data_query", "structured_query_heuristic", 0.8, "", "unknown")
    assert should_invoke_intent_llm(r, conf_threshold=0.78) is False


def test_should_invoke_mixed_marker_even_if_conf_ok():
    r = IntentRuleResult("data_query", "mixed_prefers_structured", 0.8, "", "unknown")
    assert should_invoke_intent_llm(r, conf_threshold=0.78) is True


@pytest.mark.asyncio
async def test_llm_hard_gate_images_skips_local_llm():
    with patch("app.llm.graphs.chatbot_intent_llm.ChatbotIntentLocalLlm.get_instance") as mock_get:
        r = await classify_chatbot_intent_by_llm(
            "统计缺陷",
            enable_nl2sql_route=True,
            image_urls=["http://example.com/x.jpg"],
            history_messages=None,
        )
    mock_get.assert_not_called()
    assert r.intent_label == "kb_qa"
    assert "images" in r.intent_reason


@pytest.mark.asyncio
async def test_llm_narrow_trigger_calls_local_llm(monkeypatch):
    monkeypatch.setenv("CHATBOT_INTENT_BACKEND", "llm")
    mock_runner = MagicMock()
    mock_runner.generate = AsyncMock(
        return_value='{"intent_label":"kb_qa","confidence":0.91,"reason_zh":"概念解释"}'
    )
    with patch(
        "app.llm.graphs.chatbot_intent_llm.ChatbotIntentLocalLlm.get_instance",
        return_value=mock_runner,
    ):
        r = await classify_chatbot_intent_by_llm(
            "查台账记录并解释过热原因",
            enable_nl2sql_route=True,
            image_urls=[],
            history_messages=None,
        )
    mock_runner.generate.assert_called_once()
    assert r.intent_label == "kb_qa"
    assert "intent_llm" in r.intent_reason


@pytest.mark.asyncio
async def test_llm_fallback_on_local_llm_error(monkeypatch):
    monkeypatch.setenv("CHATBOT_INTENT_BACKEND", "llm")
    mock_runner = MagicMock()
    mock_runner.generate = AsyncMock(side_effect=RuntimeError("model not loaded"))
    with patch(
        "app.llm.graphs.chatbot_intent_llm.ChatbotIntentLocalLlm.get_instance",
        return_value=mock_runner,
    ):
        with patch("app.llm.graphs.chatbot_intent_llm.get_app_config") as mock_cfg:
            mock_cfg.return_value.chatbot.intent_llm_conf_threshold = 0.78
            mock_cfg.return_value.chatbot.intent_llm_fallback_to_rules = True
            r = await classify_chatbot_intent_by_llm(
                "查台账记录并解释过热原因",
                enable_nl2sql_route=True,
                image_urls=[],
                history_messages=None,
            )
    assert "intent_llm_fallback_rules" in r.intent_reason


@pytest.mark.asyncio
async def test_clear_structured_query_stays_rules_no_local_llm():
    with patch("app.llm.graphs.chatbot_intent_llm.ChatbotIntentLocalLlm.get_instance") as mock_get:
        r = await classify_chatbot_intent_by_llm(
            "查询台账里1号炉最近一次检修记录",
            enable_nl2sql_route=True,
            image_urls=[],
            history_messages=None,
        )
    mock_get.assert_not_called()
    assert r.intent_label == "data_query"
    assert r.intent_reason == "structured_query_heuristic"
