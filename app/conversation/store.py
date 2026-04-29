from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from app.conversation.session_catalog import (
    display_title,
    session_list_limit_cap,
    strip_image_block_for_title,
    title_mode,
    truncate_for_title,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_store_lock = threading.Lock()
_store_singleton: Optional["ConversationStore"] = None


def _redis_append_wait_seconds() -> float:
    ms = max(0, int(os.getenv("CONV_REDIS_APPEND_WAIT_MS", "0")))
    return ms / 1000.0


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
        # 方案 B：user_id -> session_id -> 列表元数据（与 Redis meta 字段对齐）
        self._catalog_meta: Dict[str, Dict[str, Dict[str, Any]]] = defaultdict(dict)

    def _touch_memory_catalog(
        self, user_id: str, session_id: str, role: str, content: str, now: float
    ) -> None:
        now_ms = int(now * 1000)
        bucket = self._catalog_meta[user_id]
        meta = bucket.get(session_id)
        if meta is None:
            meta = {
                "title": "",
                "title_source": "off",
                "first_user_preview": "",
                "message_count": 0,
                "last_activity_at": now_ms,
            }
            bucket[session_id] = meta
        meta["last_activity_at"] = now_ms
        key = (user_id, session_id)
        meta["message_count"] = len(self._data.get(key, []))

        if role == "user" and not (meta.get("first_user_preview") or "").strip():
            title_content = strip_image_block_for_title(content)
            preview = title_content[:200] if len(title_content) <= 200 else title_content[:200]
            meta["first_user_preview"] = preview
            mode = title_mode()
            if mode in ("truncate", "llm"):
                meta["title"] = truncate_for_title(title_content)
                meta["title_source"] = "truncated"
            else:
                meta["title"] = ""
                meta["title_source"] = "off"

    def append_message(self, user_id: str, session_id: str, role: str, content: str) -> None:
        key = (user_id, session_id)
        messages = self._data.setdefault(key, [])
        now = time.time()
        messages.append({"role": role, "content": content, "ts": now})
        self._last_updated[key] = now
        # 历史裁剪：只保留最近 _max_history 条
        if len(messages) > self._max_history:
            self._data[key] = messages[-self._max_history :]
        self._touch_memory_catalog(user_id, session_id, role, content, now)

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
        self._catalog_meta.get(user_id, {}).pop(session_id, None)

    def delete_message(self, user_id: str, session_id: str, message_id: str) -> bool:
        """按 message_id 删除一条消息；会话为空时等价于 clear。未找到返回 False。"""
        from app.conversation.message_id import build_conversation_message_id

        key = (user_id, session_id)
        messages = self._data.get(key)
        if not messages:
            return False
        new_list: List[Dict[str, Any]] = []
        found = False
        for m in messages:
            role = str(m.get("role", ""))
            raw_c = m.get("content", "")
            c = raw_c if isinstance(raw_c, str) else (str(raw_c) if raw_c is not None else "")
            mid = build_conversation_message_id(user_id, session_id, role, c, m.get("ts"))
            if mid == message_id:
                found = True
                continue
            new_list.append(m)
        if not found:
            return False
        if not new_list:
            self.clear(user_id, session_id)
            return True
        self._data[key] = new_list
        self._last_updated[key] = time.time()
        self._sync_memory_catalog_after_messages(user_id, session_id, new_list)
        return True

    def _sync_memory_catalog_after_messages(
        self, user_id: str, session_id: str, messages: List[Dict[str, Any]]
    ) -> None:
        """删除或改写消息后同步内存目录（条数、最近活跃、非用户自定义标题）。"""
        now = time.time()
        now_ms = int(now * 1000)
        bucket = self._catalog_meta[user_id]
        meta = bucket.get(session_id)
        if meta is None:
            meta = {
                "title": "",
                "title_source": "off",
                "first_user_preview": "",
                "message_count": 0,
                "last_activity_at": now_ms,
            }
            bucket[session_id] = meta

        meta["message_count"] = len(messages)
        last_ts = messages[-1].get("ts") if messages else None
        if last_ts is not None:
            lt = float(last_ts)
            lat_ms = int(lt) if lt > 10_000_000_000 else int(lt * 1000)
        else:
            lat_ms = now_ms
        meta["last_activity_at"] = lat_ms

        if str(meta.get("title_source") or "off") == "user":
            return

        first_plain = ""
        for x in messages:
            if str(x.get("role", "")).lower() != "user":
                continue
            raw_c = x.get("content", "")
            c = raw_c if isinstance(raw_c, str) else (str(raw_c) if raw_c is not None else "")
            first_plain = strip_image_block_for_title(c)
            break
        preview = (first_plain[:200] if first_plain else "")[:200]
        meta["first_user_preview"] = preview
        mode = title_mode()
        if first_plain.strip():
            if mode in ("truncate", "llm"):
                meta["title"] = truncate_for_title(first_plain)
                meta["title_source"] = "truncated"
            else:
                meta["title"] = ""
                meta["title_source"] = "off"
        else:
            meta["title"] = ""
            meta["title_source"] = "off"

    def _memory_synthetic_meta(self, user_id: str, session_id: str) -> Dict[str, Any] | None:
        """无 catalog 条目但有历史消息时，为列表接口合成元数据（兼容升级前数据）。"""
        msgs = self._data.get((user_id, session_id))
        if not msgs:
            return None
        last_ts = msgs[-1].get("ts") or time.time()
        first_user = ""
        for x in msgs:
            if str(x.get("role", "")).lower() == "user":
                c = x.get("content", "")
                raw_user = c if isinstance(c, str) else str(c)
                first_user = strip_image_block_for_title(raw_user)
                break
        mode = title_mode()
        title = ""
        ts_src = "off"
        preview = (first_user[:200] if first_user else "")[:200]
        if first_user:
            if mode in ("truncate", "llm"):
                title = truncate_for_title(first_user)
                ts_src = "truncated"
        return {
            "title": title,
            "title_source": ts_src,
            "first_user_preview": preview,
            "message_count": len(msgs),
            "last_activity_at": int(float(last_ts) * 1000),
        }

    def list_sessions(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        order_desc: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        返回 (条目列表, 总条数)。条目含 session_id、title、title_source、last_activity_at、message_count。
        """
        cap = session_list_limit_cap()
        limit = max(1, min(limit, cap))
        offset = max(0, offset)

        sids: set[str] = set()
        for (u, sid) in self._data.keys():
            if u == user_id:
                sids.add(sid)
        sids |= set(self._catalog_meta.get(user_id, {}).keys())

        rows: List[Tuple[str, int, Dict[str, Any]]] = []
        for sid in list(sids):
            key = (user_id, sid)
            if key not in self._data:
                self._catalog_meta.get(user_id, {}).pop(sid, None)
                continue
            if self._ttl_seconds > 0:
                last = self._last_updated.get(key)
                if last is not None and (time.time() - last) > self._ttl_seconds:
                    self.clear(user_id, sid)
                    continue
            meta = self._catalog_meta.get(user_id, {}).get(sid)
            if meta is None:
                syn = self._memory_synthetic_meta(user_id, sid)
                if syn is None:
                    continue
                meta = syn
            else:
                meta = dict(meta)
            lat = int(meta.get("last_activity_at") or 0)
            rows.append((sid, lat, meta))

        rows.sort(key=lambda x: x[1], reverse=order_desc)
        total = len(rows)
        page = rows[offset : offset + limit]
        out: List[Dict[str, Any]] = []
        for sid, lat_ms, meta in page:
            disp = display_title(meta, sid)
            out.append(
                {
                    "session_id": sid,
                    "title": disp,
                    "title_source": str(meta.get("title_source") or "off"),
                    "last_activity_at": lat_ms,
                    "message_count": int(meta.get("message_count") or 0),
                }
            )
        return out, total

    def get_messages(self, user_id: str, session_id: str, limit: int | None = None) -> List[Dict[str, Any]]:
        """
        供会话管理接口读取历史（含导出）：最多返回 CONV_EXPORT_MAX_MESSAGES 条（默认 500），
        与对话上下文用的 CONV_MAX_HISTORY_MESSAGES 上限无关。
        """
        cap = max(1, int(os.getenv("CONV_EXPORT_MAX_MESSAGES", "500")))
        n = cap if limit is None else min(max(1, limit), cap)
        return self.get_recent_history(user_id, session_id, limit=n)

    def get_session_title_snapshot(self, user_id: str, session_id: str) -> Dict[str, str]:
        """
        与会话列表 `list_sessions` 同一套展示标题（`display_title`），供 GET /sessions/messages 等详情接口返回。
        返回 {"title": 展示串, "title_source": "truncated"|"off"|"user"}。
        """
        key = (user_id, session_id)
        msgs = self._data.get(key)
        meta = self._catalog_meta.get(user_id, {}).get(session_id)
        if meta is not None:
            meta_dict: Dict[str, Any] = dict(meta)
        elif msgs:
            syn = self._memory_synthetic_meta(user_id, session_id)
            meta_dict = syn if syn is not None else {}
        else:
            meta_dict = {}
        disp = display_title(meta_dict, session_id)
        return {"title": disp, "title_source": str(meta_dict.get("title_source") or "off")}

    def update_session_title(self, user_id: str, session_id: str, title: str) -> bool:
        """
        用户修改展示标题：写入 catalog / meta，`title_source=user`。
        要求会话已存在（内存中已有消息列表）。`title` 须已由上层规范化。
        """
        key = (user_id, session_id)
        if key not in self._data:
            return False
        now_ms = int(time.time() * 1000)
        now_s = str(time.time())
        bucket = self._catalog_meta[user_id]
        meta = bucket.get(session_id)
        if meta is None:
            meta = {
                "title": "",
                "title_source": "off",
                "first_user_preview": "",
                "message_count": len(self._data[key]),
                "last_activity_at": now_ms,
            }
            bucket[session_id] = meta
        meta["title"] = title
        meta["title_source"] = "user"
        meta["title_updated_at"] = now_s
        meta["last_activity_at"] = now_ms
        meta["message_count"] = len(self._data[key])
        self._last_updated[key] = time.time()
        return True


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

        connect_timeout = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))
        sock_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
        hc_interval = int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "30"))
        kw: Dict[str, Any] = {
            "encoding": "utf-8",
            "decode_responses": True,
            "socket_connect_timeout": connect_timeout,
            "socket_timeout": sock_timeout,
        }
        if hc_interval > 0:
            kw["health_check_interval"] = hc_interval
        try:
            self._redis = aioredis.from_url(redis_url, **kw)
        except TypeError:
            kw.pop("health_check_interval", None)
            self._redis = aioredis.from_url(redis_url, **kw)
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
        now = time.time()
        now_ms = int(now * 1000)
        payload = json.dumps(
            {"role": role, "content": content, "ts": now},
            ensure_ascii=False,
        )
        await self._redis.rpush(key, payload)
        if self._ttl_seconds > 0:
            await self._redis.expire(key, self._ttl_seconds)

        index_key = f"{self._key_prefix}index:{user_id}"
        meta_key = f"{self._key_prefix}meta:{user_id}:{session_id}"
        pipe = self._redis.pipeline(transaction=True)
        pipe.zadd(index_key, {session_id: now_ms})
        if self._ttl_seconds > 0:
            pipe.expire(index_key, self._ttl_seconds)
        await pipe.execute()

        if role == "user":
            prev = await self._redis.hget(meta_key, "first_user_preview")
            if not prev:
                preview = content[:200] if len(content) <= 200 else content[:200]
                mode = title_mode()
                if mode in ("truncate", "llm"):
                    title = truncate_for_title(content)
                    mapping = {
                        "first_user_preview": preview,
                        "title": title,
                        "title_source": "truncated",
                        "title_updated_at": str(now),
                    }
                else:
                    mapping = {
                        "first_user_preview": preview,
                        "title": "",
                        "title_source": "off",
                        "title_updated_at": str(now),
                    }
                await self._redis.hset(meta_key, mapping=mapping)

        try:
            await self._redis.ltrim(key, -self._max_history, -1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisConversationStore ltrim failed key=%s: %s", key, exc)

        try:
            ll = int(await self._redis.llen(key) or 0)
            await self._redis.hset(meta_key, "message_count", str(ll))
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisConversationStore meta message_count sync failed: %s", exc)

        if self._ttl_seconds > 0:
            await self._redis.expire(meta_key, self._ttl_seconds)

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
        wait_s = _redis_append_wait_seconds()
        if wait_s > 0:
            try:
                fut.result(timeout=wait_s)
            except Exception as exc:  # noqa: BLE001
                logger.error("RedisConversationStore append_message failed (sync wait): %s", exc)
            return

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
            logger.error("RedisConversationStore get_recent_history failed: %s", exc)
            return []

    @staticmethod
    def _parse_history_items(raw: List[Any], key: str) -> List[Dict[str, Any]]:
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

    async def _get_messages_async(self, user_id: str, session_id: str, n: int) -> List[Dict[str, Any]]:
        if self._redis is None:
            return super().get_messages(user_id, session_id, limit=n)
        key = f"{self._key_prefix}{user_id}:{session_id}"
        raw = await self._redis.lrange(key, -n, -1)
        return self._parse_history_items(list(raw or []), key)

    def get_messages(self, user_id: str, session_id: str, limit: int | None = None) -> List[Dict[str, Any]]:  # type: ignore[override]
        cap = max(1, int(os.getenv("CONV_EXPORT_MAX_MESSAGES", "500")))
        n = cap if limit is None else min(max(1, limit), cap)
        if self._redis is None or self._loop is None:
            return super().get_messages(user_id, session_id, limit=n)
        fut = asyncio.run_coroutine_threadsafe(
            self._get_messages_async(user_id, session_id, n),
            self._loop,
        )
        try:
            return fut.result(timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConversationStore get_messages failed: %s", exc)
            return []

    async def _get_session_title_snapshot_async(self, user_id: str, session_id: str) -> Dict[str, str]:
        if self._redis is None:
            return super().get_session_title_snapshot(user_id, session_id)
        meta_key = f"{self._key_prefix}meta:{user_id}:{session_id}"
        h = await self._redis.hgetall(meta_key)
        meta: Dict[str, Any] = dict(h) if isinstance(h, dict) else {}
        disp = display_title(meta, session_id)
        return {"title": disp, "title_source": str(meta.get("title_source") or "off")}

    def get_session_title_snapshot(self, user_id: str, session_id: str) -> Dict[str, str]:  # type: ignore[override]
        if self._redis is None or self._loop is None:
            return super().get_session_title_snapshot(user_id, session_id)
        fut = asyncio.run_coroutine_threadsafe(
            self._get_session_title_snapshot_async(user_id, session_id),
            self._loop,
        )
        try:
            return fut.result(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConversationStore get_session_title_snapshot failed: %s", exc)
            return {"title": display_title({}, session_id), "title_source": "off"}

    async def _update_session_title_async(self, user_id: str, session_id: str, title: str) -> bool:
        if self._redis is None:
            return super().update_session_title(user_id, session_id, title)
        conv_key = f"{self._key_prefix}{user_id}:{session_id}"
        if not await self._redis.exists(conv_key):
            return False
        meta_key = f"{self._key_prefix}meta:{user_id}:{session_id}"
        now = time.time()
        now_ms = int(now * 1000)
        mapping = {
            "title": title,
            "title_source": "user",
            "title_updated_at": str(now),
            "last_activity_at": str(now_ms),
        }
        try:
            ll = int(await self._redis.llen(conv_key) or 0)
            mapping["message_count"] = str(ll)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisConversationStore update_session_title message_count: %s", exc)
        await self._redis.hset(meta_key, mapping=mapping)
        if self._ttl_seconds > 0:
            try:
                await self._redis.expire(meta_key, self._ttl_seconds)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RedisConversationStore update_session_title expire meta: %s", exc)
        index_key = f"{self._key_prefix}index:{user_id}"
        try:
            await self._redis.zadd(index_key, {session_id: now_ms})
            if self._ttl_seconds > 0:
                await self._redis.expire(index_key, self._ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisConversationStore update_session_title zadd index: %s", exc)
        return True

    def update_session_title(self, user_id: str, session_id: str, title: str) -> bool:  # type: ignore[override]
        if self._redis is None or self._loop is None:
            return super().update_session_title(user_id, session_id, title)
        fut = asyncio.run_coroutine_threadsafe(
            self._update_session_title_async(user_id, session_id, title),
            self._loop,
        )
        try:
            return fut.result(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConversationStore update_session_title failed: %s", exc)
            return False

    async def _redis_refresh_meta_from_messages(
        self, user_id: str, session_id: str, items: List[Dict[str, Any]]
    ) -> None:
        """重写会话 list 后同步 meta 与 ZSET 活跃时间。"""
        if self._redis is None:
            return
        meta_key = f"{self._key_prefix}meta:{user_id}:{session_id}"
        index_key = f"{self._key_prefix}index:{user_id}"
        now_ms = int(time.time() * 1000)
        last = items[-1]
        ts_raw = last.get("ts")
        if ts_raw is not None:
            t = float(ts_raw)
            score_ms = int(t) if t > 10_000_000_000 else int(t * 1000)
        else:
            score_ms = now_ms
        ll = len(items)
        try:
            prev = await self._redis.hgetall(meta_key)
            meta_prev: Dict[str, Any] = dict(prev) if isinstance(prev, dict) else {}
        except Exception:  # noqa: BLE001
            meta_prev = {}
        title_src = str(meta_prev.get("title_source") or "off")
        mapping: Dict[str, str] = {
            "message_count": str(ll),
            "last_activity_at": str(score_ms),
        }
        if title_src != "user":
            first_plain = ""
            for x in items:
                if str(x.get("role", "")).lower() != "user":
                    continue
                raw_c = x.get("content", "")
                c = raw_c if isinstance(raw_c, str) else (str(raw_c) if raw_c is not None else "")
                first_plain = strip_image_block_for_title(c)
                break
            preview = (first_plain[:200] if first_plain else "")[:200]
            mapping["first_user_preview"] = preview
            mode = title_mode()
            if first_plain.strip():
                if mode in ("truncate", "llm"):
                    mapping["title"] = truncate_for_title(first_plain)
                    mapping["title_source"] = "truncated"
                else:
                    mapping["title"] = ""
                    mapping["title_source"] = "off"
            else:
                mapping["title"] = ""
                mapping["title_source"] = "off"
        await self._redis.hset(meta_key, mapping=mapping)
        if self._ttl_seconds > 0:
            try:
                await self._redis.expire(meta_key, self._ttl_seconds)
            except Exception as exc:  # noqa: BLE001
                logger.warning("RedisConversationStore meta expire after delete: %s", exc)
        try:
            await self._redis.zadd(index_key, {session_id: score_ms})
            if self._ttl_seconds > 0:
                await self._redis.expire(index_key, self._ttl_seconds)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisConversationStore zadd after delete: %s", exc)

    async def _delete_message_async(self, user_id: str, session_id: str, message_id: str) -> bool:
        from app.conversation.message_id import build_conversation_message_id

        if self._redis is None:
            return super().delete_message(user_id, session_id, message_id)
        key = f"{self._key_prefix}{user_id}:{session_id}"
        raw = await self._redis.lrange(key, 0, -1)
        items: List[Dict[str, Any]] = []
        for item in raw or []:
            if not isinstance(item, str):
                continue
            try:
                obj = json.loads(item)
            except json.JSONDecodeError:
                logger.warning("skip malformed conv history item key=%s snippet=%s", key, str(item)[:200])
                continue
            if isinstance(obj, dict) and obj.get("role") is not None:
                c = obj.get("content", "")
                if c is not None and str(c).strip():
                    items.append(
                        {
                            "role": str(obj["role"]),
                            "content": c if isinstance(c, str) else str(c),
                            "ts": obj.get("ts"),
                        }
                    )
        new_items: List[Dict[str, Any]] = []
        found = False
        for obj in items:
            mid = build_conversation_message_id(
                user_id,
                session_id,
                str(obj.get("role", "")),
                str(obj.get("content", "")),
                obj.get("ts"),
            )
            if mid == message_id:
                found = True
                continue
            new_items.append(obj)
        if not found:
            return False
        if not new_items:
            await self._clear_async(user_id, session_id)
            super().clear(user_id, session_id)
            return True
        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(key)
        for obj in new_items:
            payload = json.dumps(
                {"role": obj["role"], "content": obj["content"], "ts": obj["ts"]},
                ensure_ascii=False,
            )
            pipe.rpush(key, payload)
        if self._ttl_seconds > 0:
            pipe.expire(key, self._ttl_seconds)
        await pipe.execute()
        try:
            await self._redis.ltrim(key, -self._max_history, -1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RedisConversationStore delete_message ltrim: %s", exc)
        await self._redis_refresh_meta_from_messages(user_id, session_id, new_items)
        return True

    def delete_message(self, user_id: str, session_id: str, message_id: str) -> bool:  # type: ignore[override]
        if self._redis is None or self._loop is None:
            return super().delete_message(user_id, session_id, message_id)
        fut = asyncio.run_coroutine_threadsafe(
            self._delete_message_async(user_id, session_id, message_id),
            self._loop,
        )
        try:
            return bool(fut.result(timeout=10.0))
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConversationStore delete_message failed: %s", exc)
            return False

    async def _clear_async(self, user_id: str, session_id: str) -> None:
        if self._redis is None:
            return
        key = f"{self._key_prefix}{user_id}:{session_id}"
        index_key = f"{self._key_prefix}index:{user_id}"
        meta_key = f"{self._key_prefix}meta:{user_id}:{session_id}"
        pipe = self._redis.pipeline(transaction=True)
        pipe.delete(key)
        pipe.zrem(index_key, session_id)
        pipe.delete(meta_key)
        await pipe.execute()

    def clear(self, user_id: str, session_id: str) -> None:  # type: ignore[override]
        if self._redis is None or self._loop is None:
            return super().clear(user_id, session_id)
        fut = asyncio.run_coroutine_threadsafe(
            self._clear_async(user_id, session_id),
            self._loop,
        )
        try:
            fut.result(timeout=5.0)
        except Exception as exc:  # noqa: BLE001
            logger.error("RedisConversationStore clear failed: %s", exc)
            return
        super().clear(user_id, session_id)

    async def _list_sessions_async(
        self, user_id: str, limit: int, offset: int, order_desc: bool
    ) -> Tuple[List[Dict[str, Any]], int]:
        if self._redis is None:
            return super().list_sessions(user_id, limit=limit, offset=offset, order_desc=order_desc)
        index_key = f"{self._key_prefix}index:{user_id}"
        if order_desc:
            pairs = await self._redis.zrevrange(index_key, 0, -1, withscores=True)
        else:
            pairs = await self._redis.zrange(index_key, 0, -1, withscores=True)

        alive: List[Tuple[str, int]] = []
        for sid, score in pairs or []:
            conv_key = f"{self._key_prefix}{user_id}:{sid}"
            if not await self._redis.exists(conv_key):
                # 列表 key 因 TTL 已过期但 ZSET 仍残留：惰性清理，避免索引无限膨胀
                meta_key = f"{self._key_prefix}meta:{user_id}:{sid}"
                try:
                    await self._redis.zrem(index_key, sid)
                    await self._redis.delete(meta_key)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("session index orphan cleanup failed %s/%s: %s", user_id, sid, exc)
                continue
            alive.append((str(sid), int(score)))

        total = len(alive)
        page = alive[offset : offset + limit]
        if not page:
            return [], total

        pipe = self._redis.pipeline(transaction=False)
        for sid, _ in page:
            pipe.hgetall(f"{self._key_prefix}meta:{user_id}:{sid}")
        meta_rows = await pipe.execute()

        out: List[Dict[str, Any]] = []
        for (sid, lat_ms), h in zip(page, meta_rows or []):
            meta: Dict[str, Any] = dict(h) if isinstance(h, dict) else {}
            mc = meta.get("message_count")
            try:
                msg_count = int(mc) if mc not in (None, "") else 0
            except (TypeError, ValueError):
                msg_count = 0
            if msg_count <= 0:
                try:
                    msg_count = int(await self._redis.llen(f"{self._key_prefix}{user_id}:{sid}") or 0)
                except Exception:  # noqa: BLE001
                    msg_count = 0
            disp = display_title(meta, sid)
            out.append(
                {
                    "session_id": sid,
                    "title": disp,
                    "title_source": str(meta.get("title_source") or "off"),
                    "last_activity_at": lat_ms,
                    "message_count": msg_count,
                }
            )
        return out, total

    def list_sessions(  # type: ignore[override]
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        order_desc: bool = True,
    ) -> Tuple[List[Dict[str, Any]], int]:
        cap = session_list_limit_cap()
        limit = max(1, min(limit, cap))
        offset = max(0, offset)
        if self._redis is None or self._loop is None:
            return super().list_sessions(user_id, limit=limit, offset=offset, order_desc=order_desc)
        fut = asyncio.run_coroutine_threadsafe(
            self._list_sessions_async(user_id, limit, offset, order_desc),
            self._loop,
        )
        try:
            return fut.result(timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            # 勿回退到父类内存字典：Redis 模式下该字典始终为空，会误返回空列表掩盖故障
            logger.error("RedisConversationStore list_sessions failed: %s", exc)
            return [], 0


def get_default_store() -> ConversationStore:
    """
    进程内单例：所有 ``ConversationManager()`` 共享同一存储后端与（Redis 时）同一连接池/IO 线程。

    优先级：
    - 若设置了 REDIS_URL 且 redis.asyncio 可用，则使用 RedisConversationStore；
    - 否则使用内存版 ConversationStore。
    """
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    with _store_lock:
        if _store_singleton is not None:
            return _store_singleton
        redis_url: Optional[str] = os.getenv("REDIS_URL")
        if redis_url:
            try:
                _store_singleton = RedisConversationStore(redis_url)
            except Exception as exc:  # noqa: BLE001
                logger.error("failed to init RedisConversationStore, fallback to in-memory: %s", exc)
                _store_singleton = ConversationStore()
        else:
            _store_singleton = ConversationStore()
        return _store_singleton

