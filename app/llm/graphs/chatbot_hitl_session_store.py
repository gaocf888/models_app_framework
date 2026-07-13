"""智能客服 HITL：resume_token 与图状态快照（memory / redis）。"""

from __future__ import annotations

import json
import secrets
import time
from dataclasses import asdict, dataclass
from threading import Lock
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

_memory_lock = Lock()
_memory_sessions: dict[str, dict[str, Any]] = {}


@dataclass
class ChatbotHitlResumeSession:
    resume_token: str
    user_id: str
    session_id: str
    created_at: float
    hitl_kind: str
    state_snapshot: dict[str, Any]
    interrupt_payload: dict[str, Any]


def _redis_key(token: str) -> str:
    cfg = get_app_config().chatbot
    ns = (cfg.hitl_session_namespace or cfg.checkpoint_namespace or "chatbot_graph").strip()
    return f"{ns}:hitl_resume:{token}"


def _get_redis_client() -> Any | None:
    cfg = get_app_config().chatbot
    if (cfg.hitl_session_backend or "memory").lower() != "redis":
        return None
    url = (cfg.hitl_session_redis_url or cfg.checkpoint_redis_url or "").strip()
    if not url:
        import os

        url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-untyped]

        return redis.from_url(url, decode_responses=True)
    except Exception:  # noqa: BLE001
        logger.warning("chatbot hitl session_store: redis unavailable", exc_info=True)
        return None


def create_chatbot_hitl_resume_session(
    *,
    user_id: str,
    session_id: str,
    hitl_kind: str,
    state_snapshot: dict[str, Any],
    interrupt_payload: dict[str, Any],
) -> str:
    token = f"cb_rt_{secrets.token_urlsafe(24)}"
    session = ChatbotHitlResumeSession(
        resume_token=token,
        user_id=user_id,
        session_id=session_id,
        created_at=time.time(),
        hitl_kind=hitl_kind,
        state_snapshot=state_snapshot,
        interrupt_payload=interrupt_payload,
    )
    payload = asdict(session)
    ttl = max(60, int(get_app_config().chatbot.hitl_resume_ttl_seconds))
    client = _get_redis_client()
    if client is not None:
        try:
            client.setex(_redis_key(token), ttl, json.dumps(payload, ensure_ascii=False, default=str))
            return token
        except Exception:  # noqa: BLE001
            logger.warning("chatbot hitl session_store: redis set failed", exc_info=True)
    with _memory_lock:
        _memory_sessions[token] = payload
        if len(_memory_sessions) > 5000:
            oldest = sorted(_memory_sessions.items(), key=lambda x: x[1].get("created_at", 0))[:500]
            for k, _ in oldest:
                _memory_sessions.pop(k, None)
    return token


def get_chatbot_hitl_resume_session(resume_token: str) -> ChatbotHitlResumeSession | None:
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(resume_token))
            if raw:
                data = json.loads(raw)
                return ChatbotHitlResumeSession(**data)
        except Exception:  # noqa: BLE001
            logger.warning("chatbot hitl session_store: redis get failed", exc_info=True)
    with _memory_lock:
        data = _memory_sessions.get(resume_token)
    if not data:
        return None
    try:
        return ChatbotHitlResumeSession(**data)
    except TypeError:
        return None


def delete_chatbot_hitl_resume_session(resume_token: str) -> None:
    client = _get_redis_client()
    if client is not None:
        try:
            client.delete(_redis_key(resume_token))
        except Exception:  # noqa: BLE001
            logger.warning("chatbot hitl session_store: redis delete failed", exc_info=True)
    with _memory_lock:
        _memory_sessions.pop(resume_token, None)
