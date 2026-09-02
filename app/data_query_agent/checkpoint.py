"""HITL 状态检查点（memory / redis）。Sequential runner 的 interrupt/resume 源。

LangGraph RedisSaver 仅作可选能力探测；实际 payload 用与 session 相同的 JSON 键空间，
保证默认 ``checkpoint_backend=redis`` 时多 worker 可 resume。
"""

from __future__ import annotations

import json
import time
from threading import Lock
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.graphs.langgraph_redis_checkpointer import open_langgraph_redis_saver

logger = get_logger(__name__)

_memory_lock = Lock()
_memory_ckpts: dict[str, dict[str, Any]] = {}
_langgraph_saver: Any | None = None
_langgraph_probed = False
_cached_redis: Any | None = None


def build_data_query_agent_checkpointer() -> Any | None:
    """探测 LangGraph saver（供后续图化）；sequential HITL 不依赖其 channel 协议。"""
    global _langgraph_saver, _langgraph_probed
    if _langgraph_probed:
        return _langgraph_saver
    _langgraph_probed = True
    cfg = get_app_config().data_query_agent
    backend = (cfg.checkpoint_backend or "redis").strip().lower()
    if backend == "none":
        logger.info("data_query_agent: checkpoint backend=none")
        _langgraph_saver = None
        return None
    if backend == "memory":
        try:
            from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import-not-found]

            _langgraph_saver = MemorySaver()
            logger.info("data_query_agent: langgraph memory checkpointer available")
        except Exception as exc:  # noqa: BLE001
            logger.warning("data_query_agent: langgraph memory checkpointer unavailable: %s", exc)
            _langgraph_saver = None
        return _langgraph_saver
    if backend == "redis":
        url = _checkpoint_redis_url()
        if not url:
            logger.warning(
                "data_query_agent: redis checkpoint selected but URL missing; "
                "HITL snapshots fall back to in-process memory"
            )
            _langgraph_saver = None
            return None
        _langgraph_saver = open_langgraph_redis_saver(url, log_prefix="data_query_agent")
        if _langgraph_saver is not None:
            logger.info(
                "data_query_agent: redis checkpoint enabled namespace=%s",
                cfg.checkpoint_namespace,
            )
        return _langgraph_saver
    logger.warning("data_query_agent: unknown checkpoint backend=%s", backend)
    return None


def _checkpoint_redis_url() -> str:
    cfg = get_app_config().data_query_agent
    url = (cfg.checkpoint_redis_url or "").strip()
    if url:
        return url
    import os

    return (os.getenv("REDIS_URL") or "").strip()


def _ns() -> str:
    return get_app_config().data_query_agent.checkpoint_namespace or "data_query_agent"


def _ttl() -> int:
    return max(60, int(get_app_config().data_query_agent.session_ttl_seconds))


def _redis_client() -> Any | None:
    global _cached_redis
    cfg = get_app_config().data_query_agent
    backend = (cfg.checkpoint_backend or "redis").strip().lower()
    if backend != "redis":
        return None
    if _cached_redis is not None:
        return _cached_redis
    url = _checkpoint_redis_url()
    if not url:
        return None
    try:
        import redis  # type: ignore[import-untyped]

        _cached_redis = redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        return _cached_redis
    except Exception:  # noqa: BLE001
        logger.warning("data_query_agent checkpoint: redis client unavailable", exc_info=True)
        return None


def _resume_key(token: str) -> str:
    return f"{_ns()}:hitl:resume:{token}"


def _thread_key(thread_id: str) -> str:
    return f"{_ns()}:hitl:thread:{thread_id}"


def save_hitl_checkpoint(*, thread_id: str, resume_token: str, payload: dict[str, Any]) -> None:
    """写入 HITL 快照。redis 不可用时回落 memory，保证单进程仍可 resume。"""
    build_data_query_agent_checkpointer()
    body = dict(payload)
    body["thread_id"] = thread_id
    body["resume_token"] = resume_token
    body["checkpoint_at"] = time.time()
    raw = json.dumps(body, ensure_ascii=False)
    ttl = _ttl()
    client = _redis_client()
    if client is not None:
        try:
            client.setex(_resume_key(resume_token), ttl, raw)
            client.setex(_thread_key(thread_id), ttl, raw)
            logger.info(
                "data_query_agent checkpoint saved backend=redis thread_id=%s",
                thread_id,
            )
            return
        except Exception:  # noqa: BLE001
            logger.warning("data_query_agent checkpoint redis set failed, fallback memory", exc_info=True)
    with _memory_lock:
        _memory_ckpts[_resume_key(resume_token)] = body
        _memory_ckpts[_thread_key(thread_id)] = body
        if len(_memory_ckpts) > 8000:
            oldest = sorted(_memory_ckpts.items(), key=lambda x: x[1].get("checkpoint_at", 0))[:800]
            for k, _ in oldest:
                _memory_ckpts.pop(k, None)
    cfg = get_app_config().data_query_agent
    if (cfg.checkpoint_backend or "").strip().lower() == "redis":
        logger.warning("data_query_agent checkpoint using memory fallback thread_id=%s", thread_id)
    else:
        logger.info("data_query_agent checkpoint saved backend=memory thread_id=%s", thread_id)


def load_hitl_checkpoint_by_token(resume_token: str) -> dict[str, Any] | None:
    client = _redis_client()
    if client is not None:
        try:
            raw = client.get(_resume_key(resume_token))
            if raw:
                data = json.loads(raw)
                return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            logger.warning("data_query_agent checkpoint redis get failed", exc_info=True)
    with _memory_lock:
        data = _memory_ckpts.get(_resume_key(resume_token))
    return dict(data) if isinstance(data, dict) else None


def delete_hitl_checkpoint(*, thread_id: str, resume_token: str) -> None:
    client = _redis_client()
    if client is not None:
        try:
            client.delete(_resume_key(resume_token))
            client.delete(_thread_key(thread_id))
        except Exception:  # noqa: BLE001
            pass
    with _memory_lock:
        _memory_ckpts.pop(_resume_key(resume_token), None)
        _memory_ckpts.pop(_thread_key(thread_id), None)


def clear_memory_checkpoints_for_tests() -> None:
    global _langgraph_probed, _langgraph_saver, _cached_redis
    with _memory_lock:
        _memory_ckpts.clear()
    _langgraph_probed = False
    _langgraph_saver = None
    _cached_redis = None
