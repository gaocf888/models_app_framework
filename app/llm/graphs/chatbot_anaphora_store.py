"""
§4.3 会话级指代槽位 + §4.4.2 Coref 短时缓存（Redis 优先，无 Redis 时进程内）。

键空间：`conv:anaphora:{user_id}:{session_id}`、`chatbot:coref:{user_id}:{session_id}:{hq}:{ha}`。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, List

from app.core.logging import get_logger
from app.llm.graphs.chatbot_dialogue_anchor import extract_bullets_from_assistant_text
from app.llm.graphs.chatbot_anaphora_types import ANAPHORA_TYPE_CODES

logger = get_logger(__name__)

SLOT_SCHEMA_VERSION = 1
_COREF_PREFIX = "chatbot:coref:v1"

_redis_lock = threading.Lock()
_redis_client: Any | None = None
_redis_init_failed = False


def _session_ttl_seconds() -> int:
    ttl_minutes = max(0, int(os.getenv("CONV_SESSION_TTL_MINUTES", "60")))
    return max(0, ttl_minutes * 60)


def _redis_url() -> str | None:
    u = (os.getenv("REDIS_URL") or "").strip()
    return u or None


def _get_sync_redis():
    global _redis_client, _redis_init_failed
    url = _redis_url()
    if not url:
        return None
    if _redis_init_failed:
        return None
    with _redis_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            import redis as redis_sync  # type: ignore[import-untyped]

            _redis_client = redis_sync.from_url(url, decode_responses=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("anaphora_store: redis sync unavailable: %s", exc)
            _redis_init_failed = True
    return _redis_client


_slots_memory: Dict[tuple[str, str], tuple[float, Dict[str, Any]]] = {}
_coref_memory: Dict[str, tuple[float, Dict[str, Any]]] = {}
_mem_lock = threading.Lock()


def _slot_key(user_id: str, session_id: str) -> str:
    return f"conv:anaphora:{user_id}:{session_id}"


def get_anaphora_slots(user_id: str, session_id: str) -> Dict[str, Any] | None:
    r = _get_sync_redis()
    if r is not None:
        try:
            raw = r.get(_slot_key(user_id, session_id))
            if not raw:
                return None
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_anaphora_slots redis err: %s", exc)
            return None
    now = time.time()
    ttl = _session_ttl_seconds()
    with _mem_lock:
        tup = _slots_memory.get((user_id, session_id))
        if not tup:
            return None
        ts, data = tup
        if ttl > 0 and now - ts > ttl:
            del _slots_memory[(user_id, session_id)]
            return None
        return dict(data)


def save_anaphora_slots(
    user_id: str,
    session_id: str,
    payload: Dict[str, Any],
) -> None:
    ttl = _session_ttl_seconds()
    r = _get_sync_redis()
    if r is not None:
        try:
            key = _slot_key(user_id, session_id)
            if ttl > 0:
                r.setex(key, ttl, json.dumps(payload, ensure_ascii=False))
            else:
                r.set(key, json.dumps(payload, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("save_anaphora_slots redis err: %s", exc)
        return
    with _mem_lock:
        _slots_memory[(user_id, session_id)] = (time.time(), dict(payload))


def update_anaphora_slots_after_assistant(
    user_id: str,
    session_id: str,
    assistant_text: str,
    *,
    last_user_anaphora_type: str | None,
    max_bullets: int = 8,
) -> None:
    """
    assistant 落库后更新槽位（规则抽取，与 P1 v0 同源）。
    """
    bullets = extract_bullets_from_assistant_text(
        assistant_text,
        max_items=max(1, min(20, max_bullets)),
    )
    lat = (last_user_anaphora_type or "").strip()
    if lat and lat not in ANAPHORA_TYPE_CODES:
        lat = ""
    payload: Dict[str, Any] = {
        "schema_version": SLOT_SCHEMA_VERSION,
        "updated_at": int(time.time()),
        "last_assistant_bullets": bullets,
    }
    if lat:
        payload["last_anaphora_type"] = lat
    save_anaphora_slots(user_id, session_id, payload)
    invalidate_coref_cache_session(user_id, session_id)


def _norm_query(q: str, lowercase: bool) -> str:
    t = (q or "").strip()
    t = re.sub(r"\s+", " ", t)
    t = t.replace("　", " ")
    if lowercase:
        t = t.lower()
    return t


def _hash_short(s: str) -> str:
    import hashlib

    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def coref_cache_key(
    user_id: str,
    session_id: str,
    query: str,
    assistant_tail: str,
    *,
    lowercase: bool = False,
    tail_chars: int = 800,
) -> str:
    nq = _norm_query(query, lowercase)
    tail = (assistant_tail or "")[-max(50, tail_chars) :]
    return f"{_COREF_PREFIX}:{user_id}:{session_id}:{_hash_short(nq)}:{_hash_short(tail)}"


def coref_cache_get(key: str) -> Dict[str, Any] | None:
    r = _get_sync_redis()
    if r is not None:
        try:
            raw = r.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception:
            return None
    now = time.time()
    with _mem_lock:
        tup = _coref_memory.get(key)
        if not tup:
            return None
        exp, data = tup
        if now > exp:
            del _coref_memory[key]
            return None
        return dict(data)


def coref_cache_set(key: str, value: Dict[str, Any], ttl_sec: int) -> None:
    r = _get_sync_redis()
    if r is not None:
        try:
            r.setex(key, max(10, int(ttl_sec)), json.dumps(value, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            logger.warning("coref_cache_set redis err: %s", exc)
        return
    with _mem_lock:
        _coref_memory[key] = (time.time() + max(10, int(ttl_sec)), dict(value))


def invalidate_coref_cache_session(user_id: str, session_id: str) -> None:
    r = _get_sync_redis()
    prefix = f"{_COREF_PREFIX}:{user_id}:{session_id}:"
    if r is not None:
        try:
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor=cursor, match=f"{prefix}*", count=200)
                if keys:
                    r.delete(*keys)
                if cursor == 0:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("invalidate_coref_cache_session redis err: %s", exc)
        return
    with _mem_lock:
        drop = [k for k in list(_coref_memory.keys()) if k.startswith(prefix)]
        for k in drop:
            del _coref_memory[k]


def slot_bullets_list(slots: Dict[str, Any] | None) -> List[str]:
    if not slots:
        return []
    raw = slots.get("last_assistant_bullets")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]
