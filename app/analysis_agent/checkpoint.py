"""LangGraph checkpointer 工厂（memory / redis，支持多 worker）。"""

from __future__ import annotations

from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)


def build_analysis_agent_checkpointer() -> Any | None:
    """
    构建 analysis_agent 专用 checkpointer。

    - none：不启用（流式仍可用，但不支持 interrupt/resume）
    - memory：进程内（单 worker 开发）
    - redis：多 worker 共享（需 ANALYSIS_AGENT_CHECKPOINT_REDIS_URL 或 REDIS_URL）
    """
    cfg = get_app_config().analysis_agent
    backend = (cfg.checkpoint_backend or "none").strip().lower()
    if backend == "none":
        return None
    if backend == "memory":
        try:
            from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import-not-found]

            logger.info("analysis_agent: memory checkpoint enabled")
            return MemorySaver()
        except Exception as exc:  # noqa: BLE001
            logger.warning("analysis_agent: memory checkpointer unavailable: %s", exc)
            return None
    if backend == "redis":
        try:
            from langgraph.checkpoint.redis import RedisSaver  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            logger.warning("analysis_agent: redis checkpointer import failed: %s", exc)
            return None
        url = (cfg.checkpoint_redis_url or "").strip()
        if not url:
            import os

            url = (os.getenv("REDIS_URL") or "").strip()
        if not url:
            logger.warning("analysis_agent: redis checkpoint selected but URL missing")
            return None
        try:
            saver = RedisSaver.from_conn_string(url)
            logger.info(
                "analysis_agent: redis checkpoint enabled namespace=%s",
                cfg.checkpoint_namespace,
            )
            return saver
        except Exception as exc:  # noqa: BLE001
            logger.warning("analysis_agent: redis checkpointer init failed: %s", exc)
            return None
    logger.warning("analysis_agent: unknown checkpoint backend=%s", backend)
    return None


def graph_configurable(thread_id: str) -> dict[str, Any]:
    """LangGraph invoke/astream 的 configurable.thread_id。"""
    ns = get_app_config().analysis_agent.checkpoint_namespace or "analysis_agent"
    return {"configurable": {"thread_id": f"{ns}:{thread_id}"}}
