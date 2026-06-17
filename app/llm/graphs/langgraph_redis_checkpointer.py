"""LangGraph Redis checkpointer 工厂（兼容 from_conn_string 返回 context manager）。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 进程内持有 context manager，避免 __exit__ 过早关闭 Redis 连接
_redis_saver_contexts: list[AbstractContextManager[Any]] = []


def open_langgraph_redis_saver(url: str, *, log_prefix: str = "langgraph") -> Any | None:
    """
    打开 RedisSaver 供 LangGraph compile 使用。

    langgraph-checkpoint-redis 0.4+ 的 ``RedisSaver.from_conn_string`` 返回 context manager，
    不能直接传给 ``graph.compile(checkpointer=...)``。
    """
    try:
        from langgraph.checkpoint.redis import RedisSaver  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: redis checkpointer import failed: %s", log_prefix, exc)
        return None

    try:
        cm_or_saver = RedisSaver.from_conn_string(url)
        if hasattr(cm_or_saver, "__enter__"):
            saver = cm_or_saver.__enter__()
            _redis_saver_contexts.append(cm_or_saver)
        else:
            saver = cm_or_saver
        setup = getattr(saver, "setup", None)
        if callable(setup):
            setup()
        return saver
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: redis checkpointer init failed: %s", log_prefix, exc)
        return None
