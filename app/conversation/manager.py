from __future__ import annotations

from typing import Dict, List

from app.conversation.ids import validate_pair, validate_user_id
from app.conversation.store import ConversationStore, get_default_store


class ConversationManager:
    """
    会话管理器，为 Chatbot、综合分析、NL2SQL、LLM 推理等提供统一的会话接口。

    存储：
    - ``get_default_store()`` 为**进程内单例**：所有 ``ConversationManager()`` 共享同一后端与（Redis 时）同一连接池/IO 线程。
    - 各业务在 Redis 下共用 key 空间 ``conv:{user_id}:{session_id}``；不同产品线请使用不同 ``session_id`` 前缀以免混写。

    标识校验：
    - 所有入口经 ``app.conversation.ids`` 校验（长度、控制字符；``CONV_ID_STRICT`` 默认禁止 ``:``）。
    - 非法 ID 抛出 ``ConversationIdValidationError``，HTTP 映射为 **422**。

    行为边界：
    - Redis 下 ``append_message`` 默认异步；可设 ``CONV_REDIS_APPEND_WAIT_MS`` 在写后短暂阻塞等待落盘，降低读写竞态。
    - 读路径失败时不再回退到错误的进程内空视图（见 ``RedisConversationStore``）。
    """

    def __init__(self, store: ConversationStore | None = None) -> None:
        self._store = store or get_default_store()

    def append_user_message(self, user_id: str, session_id: str, content: str) -> None:
        u, s = validate_pair(user_id, session_id)
        self._store.append_message(u, s, role="user", content=content)

    def append_assistant_message(self, user_id: str, session_id: str, content: str) -> None:
        u, s = validate_pair(user_id, session_id)
        self._store.append_message(u, s, role="assistant", content=content)

    def get_recent_history(self, user_id: str, session_id: str, limit: int = 20) -> List[dict]:
        u, s = validate_pair(user_id, session_id)
        return self._store.get_recent_history(u, s, limit=limit)

    def get_session_messages(self, user_id: str, session_id: str, limit: int | None = None) -> List[dict]:
        """读取会话消息（供管理/导出），条数受 CONV_EXPORT_MAX_MESSAGES 限制。"""
        u, s = validate_pair(user_id, session_id)
        return self._store.get_messages(u, s, limit=limit)

    def get_session_title_snapshot(self, user_id: str, session_id: str) -> Dict[str, str]:
        """与 `list_sessions` 中 `title` / `title_source` 语义一致（`session_catalog.display_title`）。"""
        u, s = validate_pair(user_id, session_id)
        return self._store.get_session_title_snapshot(u, s)

    def update_session_title(self, user_id: str, session_id: str, title: str) -> bool:
        """更新会话展示标题（`title_source=user`）。会话不存在时返回 False。"""
        u, s = validate_pair(user_id, session_id)
        return self._store.update_session_title(u, s, title)

    def clear_session(self, user_id: str, session_id: str) -> None:
        """删除指定 user_id + session_id 的会话数据（Redis 或内存）。"""
        u, s = validate_pair(user_id, session_id)
        self._store.clear(u, s)

    def list_sessions(
        self,
        user_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
        order_desc: bool = True,
    ) -> tuple[list[dict], int]:
        """列举某用户的会话目录（方案 B：索引 + 元数据），返回 (items, total)。"""
        u = validate_user_id(user_id)
        return self._store.list_sessions(u, limit=limit, offset=offset, order_desc=order_desc)

