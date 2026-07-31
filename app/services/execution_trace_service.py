from __future__ import annotations

"""Execution Trace 运维查询服务。"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.models.execution_trace import (
    ExecutionTraceDegradeItem,
    ExecutionTraceDegradeTopNResponse,
    ExecutionTraceDetailResponse,
    ExecutionTraceListItem,
    ExecutionTraceListResponse,
    ExecutionTraceRecord,
    ExecutionTraceStatsResponse,
    ExecutionTraceTrendPoint,
    ExecutionTraceTrendResponse,
)
from app.services.execution_trace_store import get_execution_trace_store


class ExecutionTraceService:
    def get(self, request_id: str) -> Optional[ExecutionTraceRecord]:
        return get_execution_trace_store().get(request_id)

    def get_detail(self, request_id: str) -> ExecutionTraceDetailResponse | None:
        rec = self.get(request_id)
        if rec is None:
            return None
        return ExecutionTraceDetailResponse(ok=True, trace=rec)

    def get_result_payload(self, request_id: str) -> dict[str, Any] | None:
        """可选：按 payload_ref 拉取业务全文（当前支持 analysis 旧 Store）。"""
        rec = self.get(request_id)
        if rec is None:
            return None
        ref = rec.payload_ref or ""
        if ref.startswith("analysis:") or rec.module == "analysis":
            try:
                from app.services.analysis_trace_store import create_analysis_trace_store

                hit = create_analysis_trace_store().get(request_id)
                if hit is None:
                    return {"ok": True, "request_id": request_id, "result": None, "note": "analysis result expired or missing"}
                return {"ok": True, "request_id": request_id, "result": hit.model_dump()}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "request_id": request_id, "error": str(exc)}
        return {
            "ok": True,
            "request_id": request_id,
            "result": None,
            "note": "no full payload for this module; use Job API or module-specific endpoints",
            "trace": rec.model_dump(),
        }

    def list_traces(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        module: str | None = None,
        kind: str | None = None,
        scene: str | None = None,
        status: str | None = None,
        started_after: str | None = None,
        started_before: str | None = None,
    ) -> ExecutionTraceListResponse:
        rows, total = get_execution_trace_store().list(
            limit,
            offset,
            module=module,
            kind=kind,
            scene=scene,
            status=status,
            started_after=started_after,
            started_before=started_before,
        )
        items = [
            ExecutionTraceListItem(
                request_id=r.request_id,
                kind=r.kind,
                module=r.module,
                scene=r.scene,
                status=r.status,
                summary_preview=(r.summary or "")[:120],
                started_at=r.started_at,
                total_latency_ms=r.total_latency_ms,
            )
            for r in rows
        ]
        return ExecutionTraceListResponse(ok=True, limit=limit, offset=offset, total=total, items=items)

    def stats(
        self,
        *,
        module: str | None = None,
        kind: str | None = None,
        limit_scan: int = 2000,
    ) -> ExecutionTraceStatsResponse:
        rows, _ = get_execution_trace_store().list(limit_scan, 0, module=module, kind=kind)
        by_module: Counter[str] = Counter()
        by_kind: Counter[str] = Counter()
        by_status: Counter[str] = Counter()
        degrade: Counter[str] = Counter()
        for r in rows:
            by_module[r.module] += 1
            by_kind[r.kind] += 1
            by_status[r.status] += 1
            for reason in r.degrade_reasons or []:
                degrade[reason] += 1
        return ExecutionTraceStatsResponse(
            ok=True,
            total=len(rows),
            by_module=dict(by_module),
            by_kind=dict(by_kind),
            by_status=dict(by_status),
            degrade_reasons=dict(degrade),
        )

    def trend(self, *, bucket: str = "hour", limit_scan: int = 2000) -> ExecutionTraceTrendResponse:
        rows, _ = get_execution_trace_store().list(limit_scan, 0)
        buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: {"total": 0, "request": 0, "job": 0})
        for r in rows:
            try:
                dt = datetime.fromisoformat(r.started_at.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
            if bucket == "minute":
                key = dt.replace(second=0, microsecond=0).isoformat()
            else:
                key = dt.replace(minute=0, second=0, microsecond=0).isoformat()
            buckets[key]["total"] += 1
            buckets[key][r.kind] = buckets[key].get(r.kind, 0) + 1
        points = [
            ExecutionTraceTrendPoint(
                bucket_start=k,
                total=int(v.get("total", 0)),
                by_kind={kk: vv for kk, vv in v.items() if kk != "total"},
            )
            for k, v in sorted(buckets.items())
        ]
        return ExecutionTraceTrendResponse(ok=True, bucket="minute" if bucket == "minute" else "hour", points=points)

    def degrade_topn(self, n: int = 20, *, module: str | None = None) -> ExecutionTraceDegradeTopNResponse:
        rows, _ = get_execution_trace_store().list(2000, 0, module=module)
        c: Counter[str] = Counter()
        for r in rows:
            for reason in r.degrade_reasons or []:
                c[reason] += 1
        items = [ExecutionTraceDegradeItem(reason=k, count=v) for k, v in c.most_common(max(1, n))]
        return ExecutionTraceDegradeTopNResponse(ok=True, items=items)
