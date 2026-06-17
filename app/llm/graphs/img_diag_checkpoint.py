"""看图诊断 scope HITL LangGraph checkpointer。"""

from __future__ import annotations

from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.graphs.langgraph_redis_checkpointer import open_langgraph_redis_saver

logger = get_logger(__name__)


def _build_memory_checkpointer() -> Any | None:
    try:
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import-not-found]

        logger.info("img_diag: memory checkpoint enabled")
        return MemorySaver()
    except Exception as exc:  # noqa: BLE001
        logger.warning("img_diag: memory checkpointer unavailable: %s", exc)
        return None


def build_img_diag_checkpointer() -> Any | None:
    cfg = get_app_config().analysis
    backend = (getattr(cfg, "img_diag_checkpoint_backend", "memory") or "memory").strip().lower()
    if backend == "none":
        return None
    if backend == "memory":
        return _build_memory_checkpointer()
    if backend == "redis":
        url = (getattr(cfg, "img_diag_checkpoint_redis_url", None) or "").strip()
        if not url:
            import os

            url = (os.getenv("REDIS_URL") or "").strip()
        if not url:
            logger.warning(
                "img_diag: redis checkpoint selected but URL missing; falling back to memory"
            )
            return _build_memory_checkpointer()
        ns = getattr(cfg, "img_diag_checkpoint_namespace", "img_diag") or "img_diag"
        saver = open_langgraph_redis_saver(url, log_prefix="img_diag")
        if saver is None:
            logger.warning("img_diag: redis checkpointer unavailable; falling back to memory")
            return _build_memory_checkpointer()
        logger.info("img_diag: redis checkpoint enabled namespace=%s", ns)
        return saver
    logger.warning("img_diag: unknown checkpoint backend=%s", backend)
    return None


def img_diag_graph_configurable(thread_id: str) -> dict[str, Any]:
    cfg = get_app_config().analysis
    ns = getattr(cfg, "img_diag_checkpoint_namespace", "img_diag") or "img_diag"
    return {"configurable": {"thread_id": f"{ns}:{thread_id}"}}
