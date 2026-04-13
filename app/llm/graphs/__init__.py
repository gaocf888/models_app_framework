from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .chatbot_graph_runner import ChatbotLangGraphRunner
    from .chatbot_graph_state import ChatbotGraphState

__all__ = ["ChatbotLangGraphRunner", "ChatbotGraphState"]


def __getattr__(name: str):
    if name == "ChatbotLangGraphRunner":
        from .chatbot_graph_runner import ChatbotLangGraphRunner

        return ChatbotLangGraphRunner
    if name == "ChatbotGraphState":
        from .chatbot_graph_state import ChatbotGraphState

        return ChatbotGraphState
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
