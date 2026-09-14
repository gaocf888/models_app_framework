"""
智能客服意图分类统一入口。

按配置 ``CHATBOT_INTENT_BACKEND`` 在 ``rules``（默认）与 ``funnel``（规则→Embedding→vLLM）之间切换。
输出契约与 ``IntentRuleResult`` 保持一致。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.core.config import get_app_config
from app.core.logging import get_logger

from .chatbot_intent_funnel import classify_chatbot_intent_by_funnel
from .chatbot_intent_rules import IntentRuleResult, classify_chatbot_intent_by_rules

logger = get_logger(__name__)

_VALID_BACKENDS = frozenset({"rules", "funnel"})


def resolve_intent_backend(backend: str | None = None) -> str:
    raw = (backend or get_app_config().chatbot.intent_backend or "rules").strip().lower()
    # 兼容旧配置：llm/bert 已移除，回退 rules 并告警
    if raw in {"llm", "bert"}:
        logger.warning(
            "chatbot.intent backend=%s removed; use rules|funnel. Falling back to rules.",
            raw,
        )
        return "rules"
    if raw not in _VALID_BACKENDS:
        return "rules"
    return raw


async def classify_chatbot_intent_async(
    query: str,
    *,
    enable_nl2sql_route: bool,
    image_urls: List[str],
    history_messages: List[Dict[str, Any]] | None = None,
    backend: str | None = None,
) -> IntentRuleResult:
    """意图分类统一 API（异步）。``funnel`` 后端须使用本函数。"""
    resolved = resolve_intent_backend(backend)
    if resolved == "funnel":
        return await classify_chatbot_intent_by_funnel(
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


def classify_chatbot_intent(
    query: str,
    *,
    enable_nl2sql_route: bool,
    image_urls: List[str],
    history_messages: List[Dict[str, Any]] | None = None,
    backend: str | None = None,
) -> IntentRuleResult:
    """
    同步意图分类（仅 ``rules``）。

    ``funnel`` 在同步上下文回退为 rules，并打日志；生产路径请用 ``classify_chatbot_intent_async``。
    """
    resolved = resolve_intent_backend(backend)
    if resolved == "funnel":
        logger.warning(
            "chatbot.intent sync classify with backend=funnel; falling back to rules. "
            "Use classify_chatbot_intent_async in async paths."
        )
    return classify_chatbot_intent_by_rules(
        query,
        enable_nl2sql_route=enable_nl2sql_route,
        image_urls=image_urls,
        history_messages=history_messages,
    )
