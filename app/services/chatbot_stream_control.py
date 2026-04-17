from __future__ import annotations

import asyncio
import os
import time
import uuid
from typing import Dict, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    import redis.asyncio as aioredis  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    aioredis = None


class ChatbotStreamControl:
    """
    智能客服流式中断控制器（Redis 优先，内存回退）。

    语义：
    - `begin_stream` 生成 stream_id 并登记活动流；
    - `cancel_stream` 置位 stop 标记；
    - `is_cancelled` 在流式循环中轮询检查。
    """

    def __init__(self, ttl_seconds: int = 900) -> None:
        self._ttl = max(60, int(ttl_seconds))
        self._redis = None
        self._mem_flags: Dict[Tuple[str, str, str], float] = {}
        self._lock = asyncio.Lock()

        redis_url = (os.getenv("REDIS_URL") or "").strip()
        if redis_url and aioredis is not None:
            try:
                self._redis = aioredis.from_url(redis_url, decode_responses=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChatbotStreamControl redis init failed, fallback memory: %s", exc)

    def begin_stream(self, user_id: str, session_id: str) -> str:
        stream_id = uuid.uuid4().hex
        # 预热 key（可选），不阻塞主流程。
        if self._redis is not None:
            asyncio.create_task(self._touch_stream_async(user_id, session_id, stream_id))
        return stream_id

    async def cancel_stream(self, user_id: str, session_id: str, stream_id: str) -> None:
        if self._redis is not None:
            try:
                await self._redis.set(self._stop_key(user_id, session_id, stream_id), "1", ex=self._ttl)
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChatbotStreamControl cancel redis failed, fallback memory: %s", exc)
        async with self._lock:
            self._mem_flags[(user_id, session_id, stream_id)] = time.time() + self._ttl

    async def is_cancelled(self, user_id: str, session_id: str, stream_id: str) -> bool:
        if self._redis is not None:
            try:
                v = await self._redis.get(self._stop_key(user_id, session_id, stream_id))
                return bool(v)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChatbotStreamControl check redis failed, fallback memory: %s", exc)
        async with self._lock:
            now = time.time()
            # 惰性清理过期标记
            expired = [k for k, exp in self._mem_flags.items() if exp < now]
            for k in expired:
                self._mem_flags.pop(k, None)
            return (user_id, session_id, stream_id) in self._mem_flags

    async def clear_stream(self, user_id: str, session_id: str, stream_id: str) -> None:
        if self._redis is not None:
            try:
                await self._redis.delete(self._stop_key(user_id, session_id, stream_id))
                await self._redis.delete(self._active_key(user_id, session_id, stream_id))
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChatbotStreamControl clear redis failed, fallback memory: %s", exc)
        async with self._lock:
            self._mem_flags.pop((user_id, session_id, stream_id), None)

    async def _touch_stream_async(self, user_id: str, session_id: str, stream_id: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(self._active_key(user_id, session_id, stream_id), "1", ex=self._ttl)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ChatbotStreamControl touch redis failed: %s", exc)

    @staticmethod
    def _stop_key(user_id: str, session_id: str, stream_id: str) -> str:
        return f"chatbot:stream:stop:{user_id}:{session_id}:{stream_id}"

    @staticmethod
    def _active_key(user_id: str, session_id: str, stream_id: str) -> str:
        return f"chatbot:stream:active:{user_id}:{session_id}:{stream_id}"

