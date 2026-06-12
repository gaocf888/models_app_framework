"""
智能客服意图分类统一入口。

按配置 `CHATBOT_INTENT_BACKEND` 在规则层与 BERT 之间切换，输出契约与
`IntentRuleResult` 保持一致，供 LangGraph `intent_classify` 与 legacy 路径共用。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.config import get_app_config

from .chatbot_intent_bert import classify_chatbot_intent_by_bert
from .chatbot_intent_rules import IntentRuleResult, classify_chatbot_intent_by_rules

_VALID_BACKENDS = frozenset({"rules", "bert"})


def resolve_intent_backend(backend: str | None = None) -> str:
    raw = (backend or get_app_config().chatbot.intent_backend or "rules").strip().lower()
    if raw not in _VALID_BACKENDS:
        return "rules"
    return raw


def classify_chatbot_intent(
    query: str,
    *,
    enable_nl2sql_route: bool,
    image_urls: List[str],
    history_messages: List[Dict[str, Any]] | None = None,
    backend: str | None = None,
) -> IntentRuleResult:
    """
    意图分类统一 API。

    backend 未传时读取 `CHATBOT_INTENT_BACKEND`（默认 rules）。
    """
    resolved = resolve_intent_backend(backend)
    if resolved == "bert":
        return classify_chatbot_intent_by_bert(
            query,
            enable_nl2sql_route=enable_nl2sql_route,
            image_urls=image_urls,
            history_messages=history_messages,
        )
    return classify_chatbot_intent_by_rules(
        query,
        enable_nl2sql_route=enable_nl2sql_route,
        image_urls=image_urls,
        history_messages=history_messages,
    )
