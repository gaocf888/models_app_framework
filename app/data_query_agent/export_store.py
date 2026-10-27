"""解析成功结果快照（memory / redis），供导出回放，不重跑 NL2SQL。"""

from __future__ import annotations

import json
import secrets
import time
from threading import Lock
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

_memory_lock = Lock()
_memory_exports: dict[str, dict[str, Any]] = {}


class ExportRequestError(ValueError):
    """解析导出 token 无效、过期或身份不匹配。"""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.extra = extra or {}


def _redis_key(token: str) -> str:
    ns = get_app_config().data_query_agent.checkpoint_namespace or "data_query_agent"
    return f"{ns}:export:{token}"


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
        logger.warning("data_query_agent export_store: redis client unavailable", exc_info=True)
        return None


def save_parse_export_snapshot(
    *,
    request_id: str,
    user_id: str,
    session_id: str,
    columns: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    library_id: str = "",
    display_name: str = "",
    result_grain: str = "",
) -> str:
    """签发 dqx_ token；只存表头和行，不含 HUD 曲线。"""
    token = f"dqx_{secrets.token_urlsafe(24)}"
    payload: dict[str, Any] = {
        "export_token": token,
        "request_id": request_id,
        "user_id": user_id,
        "session_id": session_id,
        "library_id": library_id,
        "display_name": display_name,
        "result_grain": result_grain,
        "columns": list(columns or []),
        "list": list(rows or []),
        "created_at": time.time(),
    }
    ttl = max(60, int(get_app_config().data_query_agent.session_ttl_seconds))
    written = False
    client = _get_redis_client()
    if client is not None:
        try:
            client.setex(_redis_key(token), ttl, json.dumps(payload, ensure_ascii=False, default=str))
            written = True
        except Exception:  # noqa: BLE001
            logger.warning("data_query_agent export_store: redis set failed", exc_info=True)
    if not written:
        with _memory_lock:
            _memory_exports[token] = payload
            if len(_memory_exports) > 2000:
                oldest = sorted(_memory_exports.items(), key=lambda x: x[1].get("created_at", 0))[:200]
                for k, _ in oldest:
                    _memory_exports.pop(k, None)
    return token


def get_parse_export_snapshot(
    export_token: str,
    *,
    user_id: str,
    session_id: str,
) -> dict[str, Any]:
    token = (export_token or "").strip()
    if not token:
        raise ExportRequestError(422, "export_token_required", "export_token 不能为空")
    data: dict[str, Any] | None = None
    client = _get_redis_client()
    if client is not None:
        try:
            raw = client.get(_redis_key(token))
            if raw:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception:  # noqa: BLE001
            logger.warning("data_query_agent export_store: redis get failed", exc_info=True)
    if data is None:
        with _memory_lock:
            mem = _memory_exports.get(token)
            data = dict(mem) if isinstance(mem, dict) else None
    if data is None:
        raise ExportRequestError(404, "export_token_invalid", "export_token 无效或已过期")
    ttl = max(60, int(get_app_config().data_query_agent.session_ttl_seconds))
    created = float(data.get("created_at") or 0)
    if created and (time.time() - created) > ttl:
        raise ExportRequestError(404, "export_token_invalid", "export_token 无效或已过期")
    if data.get("user_id") != user_id or data.get("session_id") != session_id:
        raise ExportRequestError(403, "export_identity_mismatch", "user_id/session_id 与导出凭证不匹配")
    return data


def clear_memory_exports_for_tests() -> None:
    with _memory_lock:
        _memory_exports.clear()
