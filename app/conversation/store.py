from __future__ import annotations

import asyncio
import json
import os
import threading
import time
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
        # 会话最近更新时间与过期/裁剪策略
        self._last_updated: Dict[tuple[str, str], float] = {}
        # 会话 TTL（秒），0 或负数表示不过期
        ttl_minutes = int(os.getenv("CONV_SESSION_TTL_MINUTES", "60"))
        self._ttl_seconds = max(0, ttl_minutes * 60)
        # 单会话最大保留消息条数
        self._max_history = max(1, int(os.getenv("CONV_MAX_HISTORY_MESSAGES", "50")))

    def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        key = (user_id, session_id)
        messages = self._data.setdefault(key, [])
        now = time.time()
        messages.append({"role": role, "content": content, "ts": now})
        self._last_updated[key] = now
        # 历史裁剪：只保留最近 _max_history 条
        if len(messages) > self._max_history:
            self._data[key] = messages[-self._max_history :]

    def get_recent_history(self, user_id: str, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        key = (user_id, session_id)
        # 会话过期检查
        if self._ttl_seconds > 0:
            last = self._last_updated.get(key)
            if last is not None and (time.time() - last) > self._ttl_seconds:
                # 会话已过期，清理并返回空历史
                self.clear(user_id, session_id)
                return []
        messages = self._data.get(key, [])
        return messages[-limit:]

    def clear(self, user_id: str, session_id: str) -> None:
        key = (user_id, session_id)
        self._data.pop(key, None)
        self._last_updated.pop(key, None)


class RedisConversationStore(ConversationStore):
    """
    基于 Redis 的会话存储实现。

    使用方式：
    - 通过环境变量 `REDIS_URL` 指定连接串，例如：redis://localhost:6379/0；
    - 生产环境中建议使用该实现以支持多进程/多实例共享会话。
    """

    def __init__(self, redis_url: str) -> None:
        """
        生产可用实现：在独立事件循环线程中执行所有 Redis IO，
        避免在已有事件循环上调用 run_until_complete 带来的风险。
        """
        super().__init__()
        try:
            from redis import asyncio as aioredis  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.error("redis.asyncio not available, fallback to in-memory store: %s", exc)
            self._redis = None
            self._loop = None
            self._thread = None
            return

        self._redis = aioredis.from_url(redis_url, encoding="utf-8", decode_responses=True)
        self._key_prefix = "conv:"
        ttl_minutes = int(os.getenv("CONV_SESSION_TTL_MINUTES", "60"))
        self._ttl_seconds = max(0, ttl_minutes * 60)
        self._max_history = max(1, int(os.getenv("CONV_MAX_HISTORY_MESSAGES", "50")))

        # 为 Redis IO 创建独立事件循环线程，避免与 FastAPI 事件循环互相干扰。
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

    async def _append_message_async(self, user_id: str, session_id: str, role: str, content: str) -> None:
        if self._redis is None:
            return super().append_message(user_id, session_id, role, content)
        key = f"{self._key_prefix}{user_id}:{session_id}"
        # rpush 只接受 str/bytes 等标量；dict 会触发 redis 报错「Invalid input of type: 'dict'」
        payload = json.dumps(
            {"role": role, "content": content, "ts": time.time()},
            ensure_ascii=False,
        )
        await self._redis.rpush(key, payload)
        # 设置 TTL，让 Redis 自动过期清理
        if self._ttl_seconds > 0:
            await self._redis.expire(key, self._ttl_seconds)

    def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:  # type: ignore[override]
        """
        为了兼容当前同步接口，这里在有 Redis 时使用 fire-and-forget 的方式调度异步写入；
        若没有 Redis 或导入失败，则退回到内存实现。
        """
        if self._redis is None or self._loop is None:
            return super().append_message(user_id, session_id, role, content)
        # 将异步写入调度到专用事件循环线程，fire-and-forget
        fut = asyncio.run_coroutine_threadsafe(
            self._append_message_async(user_id, session_id, role, content),
            self._loop,
        )
        # 可选：在后台捕获异常，防止静默失败
        def _log_result(f: asyncio.Future) -> None:
            try:
                f.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("RedisConversationStore append_message failed: %s", exc)

        fut.add_done_callback(_log_result)

    async def _get_recent_history_async(self, user_id: str, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        if self._redis is None:
            return super().get_recent_history(user_id, session_id, limit)
        key = f"{self._key_prefix}{user_id}:{session_id}"
        # 取最近 limit 条记录，并限制最大条数
        real_limit = min(limit, self._max_history)
        raw = await self._redis.lrange(key, -real_limit, -1)
        out: List[Dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, str):
                continue
            try:
                obj = json.loads(item)
            except json.JSONDecodeError:
                logger.warning("skip malformed conv history item key=%s snippet=%s", key, item[:200])
                continue
            if isinstance(obj, dict) and obj.get("role") is not None:
                c = obj.get("content", "")
                if c is not None and str(c).strip():
                    out.append(
                        {
                            "role": str(obj["role"]),
                            "content": c if isinstance(c, str) else str(c),
                            "ts": obj.get("ts"),
                        }
                    )
        return out

    def get_recent_history(self, user_id: str, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:  # type: ignore[override]
        if self._redis is None or self._loop is None:
            return super().get_recent_history(user_id, session_id, limit)
        # 在独立事件循环线程上同步执行异步查询，避免在当前事件循环上阻塞
        fut = asyncio.run_coroutine_threadsafe(
            self._get_recent_history_async(user_id, session_id, limit),
            self._loop,
        )
        try:
            return fut.result(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConversationStore get_recent_history failed, fallback to in-memory: %s", exc)
            return super().get_recent_history(user_id, session_id, limit)


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

