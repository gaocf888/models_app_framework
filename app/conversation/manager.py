from __future__ import annotations

from typing import List

from app.conversation.store import ConversationStore, get_default_store


class ConversationManager:
    """
    会话管理器，为 Chatbot、综合分析、NL2SQL 等提供统一的会话接口。

    当前实现：
    - 使用内存版 ConversationStore；
    - 后续可替换为 Redis 实现，并在此封装上下文长度裁剪与摘要策略。
    """

    def __init__(self, store: ConversationStore | None = None) -> None:
        self._store = store or get_default_store()

    def append_user_message(self, user_id: str, session_id: str, content: str) -> None:
        self._store.append_message(user_id, session_id, role="user", content=content)

    def append_assistant_message(self, user_id: str, session_id: str, content: str) -> None:
        self._store.append_message(user_id, session_id, role="assistant", content=content)

    def get_recent_history(self, user_id: str, session_id: str, limit: int = 20) -> List[dict]:
        return self._store.get_recent_history(user_id, session_id, limit=limit)

