"""看图诊断 scope HITL resume_token 存储。"""

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
class ImgDiagResumeSession:
    resume_token: str
    thread_id: str
    request_id: str
    user_id: str
    session_id: str
    analysis_type: str
    img_diag_subtype: str
    created_at: float
    interrupt_payload: dict[str, Any]
    img_diag_request: dict[str, Any]
    orchestrator_path: str = "scope_first"
    vision_prefetch: dict[str, Any] | None = None
    vision_prefetch_ms: int = 0
    vision_prefetch_status: str = ""


def _redis_key(token: str) -> str:
    cfg = get_app_config().analysis
    ns = getattr(cfg, "img_diag_checkpoint_namespace", "img_diag") or "img_diag"
    return f"{ns}:resume:{token}"


def _get_redis_client() -> Any | None:
    cfg = get_app_config().analysis
    backend = (getattr(cfg, "img_diag_session_store_backend", "memory") or "memory").lower()
    if backend != "redis":
        return None
    url = (getattr(cfg, "img_diag_session_store_redis_url", None) or "").strip()
    if not url:
        import os

        url = (os.getenv("REDIS_URL") or "").strip()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-untyped]

        return redis.from_url(url, decode_responses=True)
    except Exception:  # noqa: BLE001
        logger.warning("img_diag session_store: redis unavailable", exc_info=True)
        return None


def create_img_diag_resume_token(
    *,
    thread_id: str,
    request_id: str,
    user_id: str,
    session_id: str,
    analysis_type: str,
    img_diag_subtype: str,
    interrupt_payload: dict[str, Any],
    img_diag_request: dict[str, Any],
    orchestrator_path: str = "scope_first",
    vision_prefetch: dict[str, Any] | None = None,
    vision_prefetch_ms: int = 0,
    vision_prefetch_status: str = "",
) -> str:
    token = f"rt_{secrets.token_urlsafe(24)}"
    session = ImgDiagResumeSession(
        resume_token=token,
        thread_id=thread_id,
        request_id=request_id,
        user_id=user_id,
        session_id=session_id,
        analysis_type=analysis_type,
        img_diag_subtype=img_diag_subtype,
        created_at=time.time(),
        interrupt_payload=interrupt_payload,
        img_diag_request=img_diag_request,
        orchestrator_path=orchestrator_path or "scope_first",
        vision_prefetch=vision_prefetch,
        vision_prefetch_ms=int(vision_prefetch_ms or 0),
        vision_prefetch_status=str(vision_prefetch_status or ""),
    )
    payload = asdict(session)
    ttl = max(60, int(getattr(get_app_config().analysis, "img_diag_session_ttl_seconds", 3600)))
    client = _get_redis_client()
    if client is not None:
        try:
            client.setex(_redis_key(token), ttl, json.dumps(payload, ensure_ascii=False))
            return token
        except Exception:  # noqa: BLE001
            logger.warning("img_diag session_store: redis set failed", exc_info=True)
    with _memory_lock:
        _memory_sessions[token] = payload
        if len(_memory_sessions) > 5000:
            oldest = sorted(_memory_sessions.items(), key=lambda x: x[1].get("created_at", 0))[:500]
            for k, _ in oldest:
                _memory_sessions.pop(k, None)
    return token


def get_img_diag_resume_session(resume_token: str) -> ImgDiagResumeSession | None:
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(resume_token))
            if raw:
                data = json.loads(raw)
                return ImgDiagResumeSession(**data)
        except Exception:  # noqa: BLE001
            logger.warning("img_diag session_store: redis get failed", exc_info=True)
    with _memory_lock:
        data = _memory_sessions.get(resume_token)
    if not data:
        return None
    return ImgDiagResumeSession(
        resume_token=data["resume_token"],
        thread_id=data["thread_id"],
        request_id=data["request_id"],
        user_id=data["user_id"],
        session_id=data["session_id"],
        analysis_type=data["analysis_type"],
        img_diag_subtype=data["img_diag_subtype"],
        created_at=float(data.get("created_at") or 0),
        interrupt_payload=dict(data.get("interrupt_payload") or {}),
        img_diag_request=dict(data.get("img_diag_request") or {}),
        orchestrator_path=str(data.get("orchestrator_path") or "scope_first"),
        vision_prefetch=data.get("vision_prefetch"),
        vision_prefetch_ms=int(data.get("vision_prefetch_ms") or 0),
        vision_prefetch_status=str(data.get("vision_prefetch_status") or ""),
    )


def delete_img_diag_resume_session(resume_token: str) -> None:
    client = _get_redis_client()
    if client is not None:
        try:
            client.delete(_redis_key(resume_token))
        except Exception:  # noqa: BLE001
            pass
    with _memory_lock:
        _memory_sessions.pop(resume_token, None)
