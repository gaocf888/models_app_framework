from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from threading import Lock
from typing import Any

from fastapi.responses import StreamingResponse

from app.analysis_agent.graph.runner import AnalysisAgentGraphRunner
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.models.analysis_agent import (
    AnalysisAgentResult,
    AnalysisAgentResumeRequest,
    AnalysisAgentRunRequest,
)

logger = get_logger(__name__)

_trace_lock = Lock()
_trace_store: dict[str, dict[str, Any]] = {}


def _sse_json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _encode_sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=_sse_json_default)}\n\n".encode("utf-8")


class AnalysisAgentService:
    """综合分析智能体服务门面。"""

    def __init__(self, runner: AnalysisAgentGraphRunner | None = None) -> None:
        self._runner = runner or AnalysisAgentGraphRunner()
        self._cfg = get_app_config().analysis_agent

    def _save_trace(self, result: dict[str, Any]) -> None:
        rid = result.get("request_id")
        if not rid:
            return
        with _trace_lock:
            _trace_store[rid] = result
            if len(_trace_store) > self._cfg.trace_max_items:
                for key in list(_trace_store.keys())[: max(1, len(_trace_store) - self._cfg.trace_max_items)]:
                    _trace_store.pop(key, None)
        # 旁路写入统一 Store（不改变业务 result）
        try:
            from app.models.execution_trace import ExecutionTraceRecord, TraceNode
            from app.observability.trace_recorder import save_execution_trace_record

            slot_trace = result.get("slot_trace") or result.get("trace") or {}
            nodes: list[TraceNode] = []
            if isinstance(slot_trace, dict):
                for node_id, info in slot_trace.items():
                    if isinstance(info, dict):
                        nodes.append(
                            TraceNode(
                                node_id=str(node_id),
                                status=str(info.get("status") or "success"),  # type: ignore[arg-type]
                                latency_ms=int(info.get("latency_ms") or 0) if info.get("latency_ms") is not None else None,
                                error=str(info.get("error") or "") or None,
                            )
                        )
                    else:
                        nodes.append(TraceNode(node_id=str(node_id), status="success"))
            elif isinstance(slot_trace, list):
                for item in slot_trace:
                    if isinstance(item, dict):
                        nodes.append(
                            TraceNode(
                                node_id=str(item.get("slot_id") or item.get("node_id") or "slot"),
                                status=str(item.get("status") or "success"),  # type: ignore[arg-type]
                                latency_ms=int(item["latency_ms"]) if item.get("latency_ms") is not None else None,
                            )
                        )
            save_execution_trace_record(
                ExecutionTraceRecord(
                    request_id=str(rid),
                    kind="request",
                    module="analysis_agent",
                    scene=str(result.get("analysis_type") or "") or None,
                    user_id=str(result.get("user_id") or "") or None,
                    session_id=str(result.get("session_id") or "") or None,
                    status="success",
                    started_at=str(result.get("started_at") or datetime.utcnow().isoformat()),
                    finished_at=str(result.get("finished_at") or datetime.utcnow().isoformat()),
                    total_latency_ms=int(result["total_latency_ms"]) if result.get("total_latency_ms") is not None else None,
                    nodes=nodes,
                    degrade_reasons=list(result.get("degrade_reasons") or []),
                    summary=str(result.get("summary") or "")[:512] or None,
                    meta={"source": "analysis_agent"},
                )
            )
        except Exception:  # noqa: BLE001
            pass

    def get_trace(self, request_id: str) -> dict[str, Any] | None:
        """
        对外保持 analysis_agent 业务 result 形态（进程内优先）。
        统一 Store 仅作回退，并包装为兼容结构，避免双 schema。
        """
        with _trace_lock:
            mem = _trace_store.get(request_id)
        if mem is not None:
            out = dict(mem)
            try:
                from app.services.execution_trace_store import get_execution_trace_store

                rec = get_execution_trace_store().get(request_id)
                if rec is not None:
                    out["execution_trace"] = rec.model_dump()
                    if rec.meta:
                        out.setdefault("meta", {})
                        if isinstance(out["meta"], dict):
                            for k in ("tempo_trace_id", "langsmith_run_id"):
                                if rec.meta.get(k):
                                    out["meta"][k] = rec.meta.get(k)
            except Exception:  # noqa: BLE001
                pass
            return out

        try:
            from app.services.execution_trace_store import get_execution_trace_store

            rec = get_execution_trace_store().get(request_id)
            if rec is None:
                return None
            # 兼容包装：字段对齐 AnalysisAgentResult 常用键
            return {
                "request_id": rec.request_id,
                "analysis_type": rec.scene or "",
                "summary": rec.summary or "",
                "structured_report": {},
                "evidence": {},
                "trace": {
                    "nodes": [n.model_dump() for n in (rec.nodes or [])],
                    "degrade_reasons": list(rec.degrade_reasons or []),
                },
                "slot_trace": {
                    n.node_id: {"status": n.status, "latency_ms": n.latency_ms, "error": n.error}
                    for n in (rec.nodes or [])
                },
                "user_id": rec.user_id,
                "session_id": rec.session_id,
                "started_at": rec.started_at,
                "finished_at": rec.finished_at,
                "total_latency_ms": rec.total_latency_ms,
                "degrade_reasons": list(rec.degrade_reasons or []),
                "execution_trace": rec.model_dump(),
                "meta": dict(rec.meta or {}),
            }
        except Exception:  # noqa: BLE001
            return None

    async def run_stream(self, data: AnalysisAgentRunRequest) -> StreamingResponse:
        opts = data.options.model_dump()

        async def on_complete(result: dict[str, Any]) -> None:
            self._save_trace(result)

        async def gen():
            async for ev in self._runner.iter_stream_events(
                user_id=data.user_id,
                session_id=data.session_id,
                analysis_type=data.analysis_type,
                query=data.query,
                options=opts,
                on_complete=on_complete,
            ):
                yield _encode_sse(ev)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def resume_stream(self, data: AnalysisAgentResumeRequest) -> StreamingResponse:
        async def on_complete(result: dict[str, Any]) -> None:
            self._save_trace(result)

        async def gen():
            async for ev in self._runner.iter_resume_stream_events(
                resume_token=data.resume_token,
                user_id=data.user_id,
                session_id=data.session_id,
                action=data.action,
                payload=data.payload,
                on_complete=on_complete,
            ):
                yield _encode_sse(ev)

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def resume(self, data: AnalysisAgentResumeRequest) -> AnalysisAgentResult:
        result = await self._runner.resume_to_result(
            resume_token=data.resume_token,
            user_id=data.user_id,
            session_id=data.session_id,
            action=data.action,
            payload=data.payload,
        )
        self._save_trace(result)
        return AnalysisAgentResult(
            request_id=str(result.get("request_id") or ""),
            analysis_type=str(result.get("analysis_type") or ""),
            summary=str(result.get("summary") or ""),
            structured_report=result.get("structured_report") or {},
            evidence=result.get("evidence") or {},
            trace=result.get("trace") or {},
        )
