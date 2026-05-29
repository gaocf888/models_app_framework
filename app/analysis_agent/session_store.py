"""人机协同 resume_token 注册表（memory / redis，多 worker 共享）。"""

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
class AnalysisAgentResumeSession:
    resume_token: str
    thread_id: str
    request_id: str
    user_id: str
    session_id: str
    analysis_type: str
    created_at: float
    interrupt_payload: dict[str, Any]


def _redis_key(token: str) -> str:
    ns = get_app_config().analysis_agent.checkpoint_namespace or "analysis_agent"
    return f"{ns}:resume:{token}"


def _get_redis_client() -> Any | None:
    cfg = get_app_config().analysis_agent
    if (cfg.session_store_backend or "memory").lower() != "redis":
        return None
    url = (cfg.session_store_redis_url or cfg.checkpoint_redis_url or "").strip()
    if not url:
        import os

        url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-untyped]

        return redis.from_url(url, decode_responses=True)
    except Exception:  # noqa: BLE001
        logger.warning("analysis_agent session_store: redis client unavailable", exc_info=True)
        return None


def create_resume_token(
    *,
    thread_id: str,
    request_id: str,
    user_id: str,
    session_id: str,
    analysis_type: str,
    interrupt_payload: dict[str, Any],
) -> str:
    token = f"rt_{secrets.token_urlsafe(24)}"
    session = AnalysisAgentResumeSession(
        resume_token=token,
        thread_id=thread_id,
        request_id=request_id,
        user_id=user_id,
        session_id=session_id,
        analysis_type=analysis_type,
        created_at=time.time(),
        interrupt_payload=interrupt_payload,
    )
    payload = asdict(session)
    ttl = max(60, int(get_app_config().analysis_agent.session_ttl_seconds))
    client = _get_redis_client()
    if client is not None:
        try:
            client.setex(_redis_key(token), ttl, json.dumps(payload, ensure_ascii=False))
            return token
        except Exception:  # noqa: BLE001
            logger.warning("analysis_agent session_store: redis set failed", exc_info=True)
    with _memory_lock:
        _memory_sessions[token] = payload
        if len(_memory_sessions) > 5000:
            oldest = sorted(_memory_sessions.items(), key=lambda x: x[1].get("created_at", 0))[:500]
            for k, _ in oldest:
                _memory_sessions.pop(k, None)
    return token


def get_resume_session(resume_token: str) -> AnalysisAgentResumeSession | None:
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(resume_token))
            if raw:
                data = json.loads(raw)
                return AnalysisAgentResumeSession(**data)
        except Exception:  # noqa: BLE001
            logger.warning("analysis_agent session_store: redis get failed", exc_info=True)
    with _memory_lock:
        data = _memory_sessions.get(resume_token)
    if not data:
        return None
    return AnalysisAgentResumeSession(**data)


def delete_resume_session(resume_token: str) -> None:
    client = _get_redis_client()
    if client is not None:
        try:
            client.delete(_redis_key(resume_token))
        except Exception:  # noqa: BLE001
            pass
    with _memory_lock:
        _memory_sessions.pop(resume_token, None)
