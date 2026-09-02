"""resume_token 会话（memory / redis）。

默认 session_store=redis + checkpoint_backend=redis。
无 Redis 时 session 写入回落 memory，create/update 仍双写 checkpointer。
get 在 session 未命中时回读 checkpoint。
"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass, field, fields
from threading import Lock
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.data_query_agent.checkpoint import (
    delete_hitl_checkpoint,
    load_hitl_checkpoint_by_token,
    save_hitl_checkpoint,
)

logger = get_logger(__name__)

_memory_lock = Lock()
_memory_sessions: dict[str, dict[str, Any]] = {}


@dataclass
class DataQueryResumeSession:
    """选库中断会话：create/update 双写 checkpoint，get 可回读。"""
    resume_token: str
    request_id: str
    user_id: str
    session_id: str
    query: str
    created_at: float
    hitl_attempts: int = 0
    interrupt_reason: str | None = None
    candidates: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    library_id: str | None = None


_SESSION_FIELD_NAMES = {f.name for f in fields(DataQueryResumeSession)}


def _redis_key(token: str) -> str:
    ns = get_app_config().data_query_agent.checkpoint_namespace or "data_query_agent"
    return f"{ns}:resume:{token}"


def _get_redis_client() -> Any | None:
    cfg = get_app_config().data_query_agent
    if (cfg.session_store_backend or "redis").lower() != "redis":
        return None
    url = (cfg.session_store_redis_url or cfg.checkpoint_redis_url or "").strip()
    if not url:
        import os

        url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-untyped]

        return redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
    except Exception:  # noqa: BLE001
        logger.warning("data_query_agent session_store: redis client unavailable", exc_info=True)
        return None


def _session_from_payload(data: dict[str, Any] | None) -> DataQueryResumeSession | None:
    if not isinstance(data, dict):
        return None
    kwargs = {k: v for k, v in data.items() if k in _SESSION_FIELD_NAMES}
    try:
        return DataQueryResumeSession(**kwargs)
    except TypeError:
        logger.warning("data_query_agent session payload invalid keys=%s", sorted(kwargs.keys()))
        return None


def _persist_checkpoint(session: DataQueryResumeSession) -> None:
    save_hitl_checkpoint(
        thread_id=session.request_id,
        resume_token=session.resume_token,
        payload=asdict(session),
    )


def create_resume_token(
    *,
    request_id: str,
    user_id: str,
    session_id: str,
    query: str,
    interrupt_reason: str | None,
    candidates: list[str],
    options: dict[str, Any],
    library_id: str | None = None,
    hitl_attempts: int = 0,
) -> str:
    """签发 dq_ token；session 写入后再双写 checkpoint。"""
    token = f"dq_{secrets.token_urlsafe(24)}"
    session = DataQueryResumeSession(
        resume_token=token,
        request_id=request_id,
        user_id=user_id,
        session_id=session_id,
        query=query,
        created_at=time.time(),
        hitl_attempts=hitl_attempts,
        interrupt_reason=interrupt_reason,
        candidates=list(candidates or []),
        options=dict(options or {}),
        library_id=library_id,
    )
    payload = asdict(session)
    ttl = max(60, int(get_app_config().data_query_agent.session_ttl_seconds))
    written = False
    client = _get_redis_client()
    if client is not None:
        try:
            client.setex(_redis_key(token), ttl, json.dumps(payload, ensure_ascii=False))
            written = True
        except Exception:  # noqa: BLE001
            logger.warning("data_query_agent session_store: redis set failed", exc_info=True)
    if not written:
        with _memory_lock:
            _memory_sessions[token] = payload
            if len(_memory_sessions) > 5000:
                oldest = sorted(_memory_sessions.items(), key=lambda x: x[1].get("created_at", 0))[:500]
                for k, _ in oldest:
                    _memory_sessions.pop(k, None)
    _persist_checkpoint(session)
    return token


def get_resume_session(resume_token: str) -> DataQueryResumeSession | None:
    """优先 session_store；未命中再读 HITL checkpoint（多 worker resume）。"""
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(resume_token))
            if raw:
                session = _session_from_payload(json.loads(raw))
                if session is not None:
                    return session
        except Exception:  # noqa: BLE001
            logger.warning("data_query_agent session_store: redis get failed", exc_info=True)
    with _memory_lock:
        data = _memory_sessions.get(resume_token)
    session = _session_from_payload(data)
    if session is not None:
        return session
    ckpt = load_hitl_checkpoint_by_token(resume_token)
    session = _session_from_payload(ckpt)
    if session is not None:
        logger.info(
            "data_query_agent resume from checkpoint token_prefix=%s request_id=%s",
            resume_token[:8],
            session.request_id,
        )
    return session


def update_resume_session(session: DataQueryResumeSession) -> None:
    payload = asdict(session)
    ttl = max(60, int(get_app_config().data_query_agent.session_ttl_seconds))
    written = False
    client = _get_redis_client()
    if client is not None:
        try:
            client.setex(_redis_key(session.resume_token), ttl, json.dumps(payload, ensure_ascii=False))
            written = True
        except Exception:  # noqa: BLE001
            logger.warning("data_query_agent session_store: redis update failed", exc_info=True)
    if not written:
        with _memory_lock:
            _memory_sessions[session.resume_token] = payload
    _persist_checkpoint(session)


def delete_resume_session(resume_token: str) -> None:
    payload: dict[str, Any] | None = None
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(resume_token))
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    payload = loaded
            client.delete(_redis_key(resume_token))
        except Exception:  # noqa: BLE001
            pass
    with _memory_lock:
        mem = _memory_sessions.pop(resume_token, None)
        if payload is None:
            payload = mem
    if payload is None:
        ckpt = load_hitl_checkpoint_by_token(resume_token)
        if isinstance(ckpt, dict):
            payload = ckpt
    thread_id = str((payload or {}).get("request_id") or resume_token)
    delete_hitl_checkpoint(thread_id=thread_id, resume_token=resume_token)


def clear_memory_sessions_for_tests() -> None:
    with _memory_lock:
        _memory_sessions.clear()
