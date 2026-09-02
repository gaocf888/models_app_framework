from __future__ import annotations

"""数据查询智能体流式中断（键空间 data_query_agent:stream:*）。"""

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


class DataQueryAgentStreamControl:
    """协作取消：stop 写 flag，acquire 前/中轮询；键空间独立于分析智能体。"""
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
                logger.warning("DataQueryAgentStreamControl redis init failed, fallback memory: %s", exc)

    def begin_stream(self, user_id: str, session_id: str) -> str:
        stream_id = uuid.uuid4().hex
        if self._redis is not None:
            asyncio.create_task(self._touch_stream_async(user_id, session_id, stream_id))
        return stream_id

    async def cancel_stream(self, user_id: str, session_id: str, stream_id: str) -> None:
        if self._redis is not None:
            try:
                await self._redis.set(self._stop_key(user_id, session_id, stream_id), "1", ex=self._ttl)
                return
            except Exception as trans_exc:  # noqa: BLE001
                logger.warning("DataQueryAgentStreamControl cancel redis failed, fallback memory: %s", trans_exc)
        async with self._lock:
            self._mem_flags[(user_id, session_id, stream_id)] = time.time() + self._ttl

    async def is_cancelled(self, user_id: str, session_id: str, stream_id: str) -> bool:
        if self._redis is not None:
            try:
                v = await self._redis.get(self._stop_key(user_id, session_id, stream_id))
                return bool(v)
            except Exception as trans_exc:  # noqa: BLE001
                logger.warning("DataQueryAgentStreamControl check redis failed, fallback memory: %s", trans_exc)
        async with self._lock:
            now = time.time()
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
            except Exception as trans_exc:  # noqa: BLE001
                logger.warning("DataQueryAgentStreamControl clear redis failed, fallback memory: %s", trans_exc)
        async with self._lock:
            self._mem_flags.pop((user_id, session_id, stream_id), None)

    async def _touch_stream_async(self, user_id: str, session_id: str, stream_id: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(self._active_key(user_id, session_id, stream_id), "1", ex=self._ttl)
        except Exception as trans_exc:  # noqa: BLE001
            logger.warning("DataQueryAgentStreamControl touch redis failed: %s", trans_exc)

    @staticmethod
    def _stop_key(user_id: str, session_id: str, stream_id: str) -> str:
        return f"data_query_agent:stream:stop:{user_id}:{session_id}:{stream_id}"

    @staticmethod
    def _active_key(user_id: str, session_id: str, stream_id: str) -> str:
        return f"data_query_agent:stream:active:{user_id}:{session_id}:{stream_id}"
