from __future__ import annotations

"""ExecutionTrace Store 元数据补丁（OTLP/LangSmith 回写）。"""

from typing import Any, Dict

from app.core.logging import get_logger
from app.services.execution_trace_store import get_execution_trace_store

logger = get_logger(__name__)


def patch_execution_trace_meta(request_id: str, updates: Dict[str, Any]) -> None:
    """合并 meta 字段并重新 save；失败仅打日志。"""
    if not request_id or not updates:
        return
    try:
        store = get_execution_trace_store()
        rec = store.get(request_id)
        if rec is None:
            return
        meta = dict(rec.meta or {})
        meta.update({k: v for k, v in updates.items() if v is not None})
        rec.meta = meta
        store.save(rec)
    except Exception as exc:  # noqa: BLE001
        logger.warning("patch_execution_trace_meta failed rid=%s: %s", request_id, exc)
