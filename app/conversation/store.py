from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


class ConversationStore:
    """
    会话存储抽象的内存实现。

    - 默认使用进程内字典存储，适用于开发与单进程运行；
    - 生产环境建议使用 RedisConversationStore（见下方）。
    """

    def __init__(self) -> None:
        # 简单内存存储: {(user_id, session_id): [messages]}
        self._data: Dict[tuple[str, str], List[Dict[str, Any]]] = {}

    def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        key = (user_id, session_id)
        self._data.setdefault(key, []).append({"role": role, "content": content})

    def get_recent_history(self, user_id: str, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        key = (user_id, session_id)
        messages = self._data.get(key, [])
        return messages[-limit:]

    def clear(self, user_id: str, session_id: str) -> None:
        key = (user_id, session_id)
        self._data.pop(key, None)


class RedisConversationStore(ConversationStore):
    """
    基于 Redis 的会话存储实现。

    使用方式：
    - 通过环境变量 `REDIS_URL` 指定连接串，例如：redis://localhost:6379/0；
    - 生产环境中建议使用该实现以支持多进程/多实例共享会话。
    """

    def __init__(self, redis_url: str) -> None:
        super().__init__()
        try:
            from redis import asyncio as aioredis  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.error("redis.asyncio not available, fallback to in-memory store: %s", exc)
            self._redis = None
            return

        self._redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        self._key_prefix = "conv:"

    async def _append_message_async(self, user_id: str, session_id: str, role: str, content: str) -> None:
        if self._redis is None:
            return super().append_message(user_id, session_id, role, content)
        key = f"{self._key_prefix}{user_id}:{session_id}"
        await self._redis.rpush(key, {"role": role, "content": content})

    def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:  # type: ignore[override]
        """
        为了兼容当前同步接口，这里在有 Redis 时使用 fire-and-forget 的方式调度异步写入；
        若没有 Redis 或导入失败，则退回到内存实现。
        """
        if self._redis is None:
            return super().append_message(user_id, session_id, role, content)
        import asyncio

        asyncio.create_task(self._append_message_async(user_id, session_id, role, content))

    async def _get_recent_history_async(self, user_id: str, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        if self._redis is None:
            return super().get_recent_history(user_id, session_id, limit)
        key = f"{self._key_prefix}{user_id}:{session_id}"
        # 取最近 limit 条记录
        raw = await self._redis.lrange(key, -limit, -1)
        return list(raw or [])

    def get_recent_history(self, user_id: str, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:  # type: ignore[override]
        if self._redis is None:
            return super().get_recent_history(user_id, session_id, limit)
        import asyncio

        return asyncio.get_event_loop().run_until_complete(
            self._get_recent_history_async(user_id, session_id, limit)
        )


def get_default_store() -> ConversationStore:
    """
    根据配置选择默认的会话存储实现。

    优先级：
    - 若设置了 REDIS_URL 且 redis.asyncio 可用，则使用 RedisConversationStore；
    - 否则使用内存版 ConversationStore。
    """
    redis_url: Optional[str] = os.getenv("REDIS_URL")
    if redis_url:
        try:
            return RedisConversationStore(redis_url)
        except Exception as exc:  # noqa: BLE001
            logger.error("failed to init RedisConversationStore, fallback to in-memory: %s", exc)
    return ConversationStore()

