"""chatbot_intent_llm 模式 B 与窄触发单测。"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.graphs.chatbot_intent_llm import (
    _build_intent_llm_messages,
    classify_chatbot_intent_by_llm,
    resolve_intent_llm_trigger,
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
    assert resolve_intent_llm_trigger(r, conf_threshold=0.78) == "mixed"


def test_resolve_trigger_low_confidence_default_kb_qa():
    r = IntentRuleResult("kb_qa", "default_kb_qa", 0.82, "", "text_kb_qa")
    assert resolve_intent_llm_trigger(r, conf_threshold=0.83) == "low_confidence"
    assert resolve_intent_llm_trigger(r, conf_threshold=0.78) is None


def test_resolve_trigger_ambiguous_over_low_confidence():
    r = IntentRuleResult("kb_qa", "ambiguous_pattern_resolved_by_ctx|ctx_task=text_kb_qa", 0.72, "h", "text_kb_qa")
    assert resolve_intent_llm_trigger(r, conf_threshold=0.78) == "ambiguous_ctx"


def test_build_messages_low_confidence_omits_rule_label():
    msgs = _build_intent_llm_messages(
        query="1号机组管子数量",
        history_summary="",
        enable_nl2sql_route=True,
        trigger="low_confidence",
    )
    sys = msgs[0]["content"]
    assert "规则层初判" not in sys
    assert "独立分类" in sys
    assert "default_kb_qa" not in sys
    assert "1号机组管子数量" in sys
    assert "data_query" in sys


def test_build_messages_mixed_weak_hint_no_rule_label():
    msgs = _build_intent_llm_messages(
        query="查台账并解释过热原因",
        history_summary="",
        enable_nl2sql_route=True,
        trigger="mixed",
    )
    sys = msgs[0]["content"]
    assert "混合意图" in sys
    assert "规则层初判" not in sys
    assert "mixed_prefers" not in sys


def test_build_messages_ambiguous_weak_hint():
    msgs = _build_intent_llm_messages(
        query="上述原因",
        history_summary="assistant: 过热原因…",
        enable_nl2sql_route=True,
        trigger="ambiguous_ctx",
    )
    sys = msgs[0]["content"]
    assert "指代上文" in sys
    assert "规则层初判" not in sys


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
async def test_llm_logs_raw_on_json_parse_failure(caplog):
    mock_runner = MagicMock()
    mock_runner.generate = AsyncMock(return_value="抱歉，我无法判断意图")
    with patch(
        "app.llm.graphs.chatbot_intent_llm.ChatbotIntentLocalLlm.get_instance",
        return_value=mock_runner,
    ):
        with patch("app.llm.graphs.chatbot_intent_llm.get_app_config") as mock_cfg:
            mock_cfg.return_value.chatbot.intent_llm_conf_threshold = 0.78
            mock_cfg.return_value.chatbot.intent_llm_fallback_to_rules = True
            with caplog.at_level(logging.DEBUG, logger="app.llm.graphs.chatbot_intent_llm"):
                r = await classify_chatbot_intent_by_llm(
                    "查台账记录并解释过热原因",
                    enable_nl2sql_route=True,
                    image_urls=[],
                    history_messages=None,
                )
    assert "intent_llm_fallback_rules" in r.intent_reason
    assert "json_parse_failed" in caplog.text
    assert "抱歉，我无法判断意图" in caplog.text


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
