from __future__ import annotations

"""Trace 脱敏与体积控制。"""

import hashlib
import re
from typing import Any, Dict, List, Optional

from app.models.execution_trace import ExecutionTraceRecord, TraceNode
from app.observability.settings import get_execution_trace_settings

_SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|cookie|private[_-]?key)",
    re.IGNORECASE,
)


def truncate_text(value: Optional[str], max_chars: int | None = None) -> Optional[str]:
    if value is None:
        return None
    limit = max_chars if max_chars is not None else get_execution_trace_settings().query_max_chars
    if len(value) <= limit:
        return value
    return value[:limit] + f"...(truncated,{len(value)})"


def sha256_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def sanitize_mapping(data: Dict[str, Any], *, max_chars: int | None = None) -> Dict[str, Any]:
    limit = max_chars if max_chars is not None else get_execution_trace_settings().query_max_chars
    out: Dict[str, Any] = {}
    for key, value in (data or {}).items():
        if _SECRET_KEY_RE.search(str(key)):
            out[str(key)] = "***"
            continue
        if isinstance(value, str):
            out[str(key)] = truncate_text(value, limit)
        elif isinstance(value, dict):
            out[str(key)] = sanitize_mapping(value, max_chars=limit)
        elif isinstance(value, list):
            # 列表只保留短摘要，避免大对象
            if len(value) > 20:
                out[str(key)] = {"_type": "list", "len": len(value)}
            else:
                out[str(key)] = [
                    truncate_text(str(x), min(256, limit)) if not isinstance(x, (int, float, bool, type(None))) else x
                    for x in value
                ]
        else:
            out[str(key)] = value
    return out


def sanitize_node(node: TraceNode) -> TraceNode:
    limit = get_execution_trace_settings().query_max_chars
    return TraceNode(
        node_id=node.node_id,
        status=node.status,
        latency_ms=node.latency_ms,
        started_at=node.started_at,
        finished_at=node.finished_at,
        error=truncate_text(node.error, min(512, limit)),
        attributes=sanitize_mapping(node.attributes or {}, max_chars=min(512, limit)),
    )


def sanitize_record(record: ExecutionTraceRecord) -> ExecutionTraceRecord:
    limit = get_execution_trace_settings().query_max_chars
    nodes: List[TraceNode] = [sanitize_node(n) for n in (record.nodes or [])]
    return ExecutionTraceRecord(
        request_id=record.request_id,
        kind=record.kind,
        module=record.module,
        scene=record.scene,
        user_id=record.user_id,
        session_id=record.session_id,
        status=record.status,
        started_at=record.started_at,
        finished_at=record.finished_at,
        total_latency_ms=record.total_latency_ms,
        nodes=nodes,
        degrade_reasons=list(record.degrade_reasons or [])[:50],
        summary=truncate_text(record.summary, min(512, limit)),
        meta=sanitize_mapping(record.meta or {}, max_chars=limit),
        payload_ref=record.payload_ref,
    )
