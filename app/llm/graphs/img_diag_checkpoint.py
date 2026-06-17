"""看图诊断 scope HITL LangGraph checkpointer。"""

from __future__ import annotations

from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_img_diag_checkpointer() -> Any | None:
    cfg = get_app_config().analysis
    backend = (getattr(cfg, "img_diag_checkpoint_backend", "memory") or "memory").strip().lower()
    if backend == "none":
        return None
    if backend == "memory":
        try:
            from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import-not-found]

            logger.info("img_diag: memory checkpoint enabled")
            return MemorySaver()
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag: memory checkpointer unavailable: %s", exc)
            return None
    if backend == "redis":
        try:
            from langgraph.checkpoint.redis import RedisSaver  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag: redis checkpointer import failed: %s", exc)
            return None
        url = (getattr(cfg, "img_diag_checkpoint_redis_url", None) or "").strip()
        if not url:
            import os

            url = (os.getenv("REDIS_URL") or "").strip()
        if not url:
            logger.warning("img_diag: redis checkpoint selected but URL missing")
            return None
        try:
            saver = RedisSaver.from_conn_string(url)
            ns = getattr(cfg, "img_diag_checkpoint_namespace", "img_diag") or "img_diag"
            logger.info("img_diag: redis checkpoint enabled namespace=%s", ns)
            return saver
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag: redis checkpointer init failed: %s", exc)
            return None
    logger.warning("img_diag: unknown checkpoint backend=%s", backend)
    return None


def img_diag_graph_configurable(thread_id: str) -> dict[str, Any]:
    cfg = get_app_config().analysis
    ns = getattr(cfg, "img_diag_checkpoint_namespace", "img_diag") or "img_diag"
    return {"configurable": {"thread_id": f"{ns}:{thread_id}"}}
