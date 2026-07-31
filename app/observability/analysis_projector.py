from __future__ import annotations

"""AnalysisV2Result → ExecutionTraceRecord 投影（双写，不改业务结果）。"""

from datetime import datetime, timezone
from typing import Any

from app.models.analysis import AnalysisV2Result
from app.models.execution_trace import ExecutionTraceRecord, TraceNode
from app.observability.settings import get_execution_trace_settings
from app.observability.trace_recorder import save_execution_trace_record


def project_analysis_result(result: AnalysisV2Result) -> ExecutionTraceRecord | None:
    cfg = get_execution_trace_settings()
    if not cfg.enabled or not cfg.analysis_dual_write:
        return None
    if "analysis" not in cfg.modules:
        return None

    trace = result.trace
    nodes: list[TraceNode] = []
    latency = dict(trace.node_latency_ms or {})
    status_map = dict(trace.node_status or {})
    # 保序：latency keys 优先，再补 status
    ordered = list(latency.keys())
    for k in status_map.keys():
        if k not in ordered:
            ordered.append(k)
    for node_id in ordered:
        nodes.append(
            TraceNode(
                node_id=node_id,
                status=(status_map.get(node_id) or "success"),  # type: ignore[arg-type]
                latency_ms=int(latency.get(node_id) or 0) if node_id in latency else None,
            )
        )

    total_ms = sum(int(v or 0) for v in latency.values()) if latency else None
    started = datetime.now(timezone.utc).isoformat()
    data_mode = None
    try:
        data_mode = str(result.evidence.data_coverage.get("mode") or "") or None
    except Exception:  # noqa: BLE001
        data_mode = None

    record = ExecutionTraceRecord(
        request_id=result.request_id,
        kind="request",
        module="analysis",
        scene=str(result.analysis_type),
        status="success",
        started_at=started,
        finished_at=started,
        total_latency_ms=total_ms,
        nodes=nodes,
        degrade_reasons=list(trace.degrade_reasons or []),
        summary=(result.summary or "")[:512],
        meta={
            "plan_id": trace.plan_id,
            "template_versions": dict(trace.template_versions or {}),
            "execution_summary": dict(trace.execution_summary or {}),
            "data_plan_trace": list(trace.data_plan_trace or [])[:50],
            "data_mode": data_mode,
        },
        payload_ref=f"analysis:{result.request_id}",
    )
    save_execution_trace_record(record, finalize_side_effects=True)
    return record
