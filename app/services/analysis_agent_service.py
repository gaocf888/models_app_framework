from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
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
    AnalysisAgentStreamStopRequest,
    AnalysisAgentStreamStopResponse,
    AnalysisAgentTraceDegradeItem,
    AnalysisAgentTraceDegradeTopNResponse,
    AnalysisAgentTraceListItem,
    AnalysisAgentTraceStatsResponse,
    AnalysisAgentTraceTrendPoint,
    AnalysisAgentTraceTrendResponse,
)
from app.services.analysis_agent_stream_control import AnalysisAgentStreamControl
from app.services.analysis_agent_trace_store import (
    AnalysisAgentTraceStore,
    create_analysis_agent_trace_store,
)

logger = get_logger(__name__)

_trend_cache_lock = Lock()
_trend_cache: dict[str, tuple[float, AnalysisAgentTraceTrendResponse]] = {}
_TREND_CACHE_TTL_S = 15.0


def _sse_json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _encode_sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=_sse_json_default)}\n\n".encode("utf-8")


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AnalysisAgentService:
    """综合分析智能体服务门面。"""

    def __init__(
        self,
        runner: AnalysisAgentGraphRunner | None = None,
        stream_control: AnalysisAgentStreamControl | None = None,
        trace_store: AnalysisAgentTraceStore | None = None,
    ) -> None:
        self._stream_ctrl = stream_control or AnalysisAgentStreamControl()
        # 默认懒加载 runner，避免仅查 Trace 时初始化 RAG/NL2SQL
        self._runner: AnalysisAgentGraphRunner | None = runner
        self._cfg = get_app_config().analysis_agent
        self._trace_store = trace_store or create_analysis_agent_trace_store(
            backend=self._cfg.trace_backend,
            ttl_minutes=self._cfg.trace_ttl_minutes,
            max_items=self._cfg.trace_max_items,
            es_hosts=self._cfg.trace_es_hosts or None,
            es_index=self._cfg.trace_es_index or None,
        )

    @property
    def _runner_or_create(self) -> AnalysisAgentGraphRunner:
        if self._runner is None:
            self._runner = AnalysisAgentGraphRunner(stream_control=self._stream_ctrl)
        return self._runner

    async def stop_stream(
        self, user_id: str, session_id: str, stream_id: str
    ) -> AnalysisAgentStreamStopResponse:
        await self._stream_ctrl.cancel_stream(user_id, session_id, stream_id)
        return AnalysisAgentStreamStopResponse(ok=True, stream_id=stream_id)

    def _enrich_trace_record(self, result: dict[str, Any]) -> dict[str, Any]:
        out = dict(result)
        now = _now_iso()
        out.setdefault("started_at", now)
        out.setdefault("finished_at", now)
        if out.get("total_latency_ms") is None:
            trace = out.get("trace") if isinstance(out.get("trace"), dict) else {}
            if trace.get("total_ms") is not None:
                out["total_latency_ms"] = int(trace["total_ms"])
        return out

    def _save_trace(self, result: dict[str, Any]) -> None:
        rid = result.get("request_id")
        if not rid:
            return
        record = self._enrich_trace_record(result)
        try:
            self._trace_store.save(record)
        except Exception:  # noqa: BLE001
            logger.exception("analysis_agent trace save failed request_id=%s", rid)
        # 旁路写入统一 Store（不改变业务 result）
        try:
            from app.models.execution_trace import ExecutionTraceRecord, TraceNode
            from app.observability.trace_recorder import save_execution_trace_record

            slot_trace = record.get("slot_trace") or record.get("trace") or {}
            nodes: list[TraceNode] = []
            if isinstance(slot_trace, dict):
                for node_id, info in slot_trace.items():
                    if isinstance(info, dict):
                        nodes.append(
                            TraceNode(
                                node_id=str(node_id),
                                status=str(info.get("status") or "success"),  # type: ignore[arg-type]
                                latency_ms=int(info.get("latency_ms") or 0)
                                if info.get("latency_ms") is not None
                                else None,
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
                                latency_ms=int(item["latency_ms"])
                                if item.get("latency_ms") is not None
                                else None,
                            )
                        )
            status = "success"
            tr = record.get("trace") if isinstance(record.get("trace"), dict) else {}
            if tr.get("status") in {"aborted", "failed"}:
                status = str(tr.get("status"))
            save_execution_trace_record(
                ExecutionTraceRecord(
                    request_id=str(rid),
                    kind="request",
                    module="analysis_agent",
                    scene=str(record.get("analysis_type") or "") or None,
                    user_id=str(record.get("user_id") or "") or None,
                    session_id=str(record.get("session_id") or "") or None,
                    status=status,  # type: ignore[arg-type]
                    started_at=str(record.get("started_at") or _now_iso()),
                    finished_at=str(record.get("finished_at") or _now_iso()),
                    total_latency_ms=int(record["total_latency_ms"])
                    if record.get("total_latency_ms") is not None
                    else None,
                    nodes=nodes,
                    degrade_reasons=list(record.get("degrade_reasons") or []),
                    summary=str(record.get("summary") or "")[:512] or None,
                    meta={"source": "analysis_agent"},
                )
            )
        except Exception:  # noqa: BLE001
            pass

    def get_trace(self, request_id: str) -> dict[str, Any] | None:
        """
        对外保持 analysis_agent 业务 result 形态。
        统一 Store 仅作回退，并包装为兼容结构，避免双 schema。
        """
        try:
            mem = self._trace_store.get(request_id)
        except Exception:  # noqa: BLE001
            logger.exception("analysis_agent trace get failed request_id=%s", request_id)
            mem = None
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

    @staticmethod
    def _to_list_item(row: dict[str, Any]) -> AnalysisAgentTraceListItem:
        summary = str(row.get("summary") or "")
        tr = row.get("trace") if isinstance(row.get("trace"), dict) else {}
        status = str(tr.get("status") or "success")
        degrade = list(row.get("degrade_reasons") or tr.get("degrade_reasons") or [])
        return AnalysisAgentTraceListItem(
            request_id=str(row.get("request_id") or ""),
            analysis_type=str(row.get("analysis_type") or ""),
            summary_preview=summary[:120],
            created_at=str(row.get("started_at") or row.get("finished_at") or ""),
            status=status,
            user_id=str(row.get("user_id") or ""),
            degrade_count=len([r for r in degrade if r]),
        )

    def list_traces(
        self,
        *,
        limit: int,
        offset: int,
        analysis_type: str | None = None,
        user_id: str | None = None,
        request_id_like: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
    ) -> tuple[list[AnalysisAgentTraceListItem], int]:
        from_dt = _parse_iso8601(started_from)
        to_dt = _parse_iso8601(started_to)
        score_min_ms = int(from_dt.timestamp() * 1000) if from_dt else None
        score_max_ms = int(to_dt.timestamp() * 1000) if to_dt else None
        fetch_limit = min(1000, max(200, offset + limit + 200))
        items, total_from_store = self._trace_store.list(
            limit=fetch_limit,
            offset=0,
            score_min_ms=score_min_ms,
            score_max_ms=score_max_ms,
            analysis_type=analysis_type,
            user_id=user_id,
        )
        filtered: list[dict[str, Any]] = []
        for x in items:
            if request_id_like and request_id_like not in str(x.get("request_id") or ""):
                continue
            filtered.append(x)
        # 类型/用户已在 store 过滤；request_id_like 为窗口内二次过滤
        total = len(filtered) if request_id_like else total_from_store
        page = filtered[offset : offset + limit]
        return [self._to_list_item(x) for x in page], total

    def get_trace_stats(
        self,
        *,
        analysis_type: str | None = None,
        user_id: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
    ) -> AnalysisAgentTraceStatsResponse:
        from_dt = _parse_iso8601(started_from)
        to_dt = _parse_iso8601(started_to)
        raw, total = self._trace_store.list(
            limit=1000,
            offset=0,
            score_min_ms=int(from_dt.timestamp() * 1000) if from_dt else None,
            score_max_ms=int(to_dt.timestamp() * 1000) if to_dt else None,
            analysis_type=analysis_type,
            user_id=user_id,
        )
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        degrade: dict[str, int] = {}
        for x in raw:
            atype = str(x.get("analysis_type") or "")
            by_type[atype] = by_type.get(atype, 0) + 1
            tr = x.get("trace") if isinstance(x.get("trace"), dict) else {}
            status = str(tr.get("status") or "success")
            by_status[status] = by_status.get(status, 0) + 1
            reasons = list(x.get("degrade_reasons") or tr.get("degrade_reasons") or [])
            for reason in reasons:
                if not reason:
                    continue
                degrade[str(reason)] = degrade.get(str(reason), 0) + 1
        return AnalysisAgentTraceStatsResponse(
            ok=True,
            total=total,
            by_analysis_type=by_type,
            by_status=by_status,
            degrade_reasons=degrade,
        )

    def get_trace_trend(
        self,
        *,
        bucket: str = "hour",
        analysis_type: str | None = None,
        user_id: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
    ) -> AnalysisAgentTraceTrendResponse:
        if bucket not in {"minute", "hour"}:
            bucket = "hour"
        cache_key = "|".join(
            [
                str(bucket),
                str(analysis_type or ""),
                str(user_id or ""),
                str(started_from or ""),
                str(started_to or ""),
            ]
        )
        with _trend_cache_lock:
            hit = _trend_cache.get(cache_key)
            if hit and (time.time() - hit[0]) <= _TREND_CACHE_TTL_S:
                return hit[1]

        from_dt = _parse_iso8601(started_from)
        to_dt = _parse_iso8601(started_to)
        score_min_ms = int(from_dt.timestamp() * 1000) if from_dt else None
        score_max_ms = int(to_dt.timestamp() * 1000) if to_dt else None
        items, _ = self._trace_store.list(
            limit=5000,
            offset=0,
            score_min_ms=score_min_ms,
            score_max_ms=score_max_ms,
            analysis_type=analysis_type,
            user_id=user_id,
        )
        agg: dict[datetime, dict[str, int]] = {}
        for x in items:
            started_at = _parse_iso8601(str(x.get("started_at") or x.get("finished_at") or ""))
            if started_at is None:
                continue
            ts = started_at.astimezone(timezone.utc)
            key = ts.replace(second=0, microsecond=0)
            if bucket == "hour":
                key = key.replace(minute=0)
            atype = str(x.get("analysis_type") or "unknown")
            row = agg.setdefault(key, {"total": 0})
            row["total"] += 1
            row[atype] = row.get(atype, 0) + 1

        points: list[AnalysisAgentTraceTrendPoint] = []
        for k in sorted(agg.keys()):
            row = agg[k]
            by_type = {kk: int(vv) for kk, vv in row.items() if kk != "total"}
            points.append(
                AnalysisAgentTraceTrendPoint(
                    bucket_start=k.isoformat().replace("+00:00", "Z"),
                    total=int(row.get("total", 0)),
                    by_analysis_type=by_type,
                )
            )
        result = AnalysisAgentTraceTrendResponse(
            ok=True,
            bucket="minute" if bucket == "minute" else "hour",
            points=points,
        )
        with _trend_cache_lock:
            _trend_cache[cache_key] = (time.time(), result)
        return result

    def get_degrade_topn(
        self,
        *,
        top_n: int = 10,
        analysis_type: str | None = None,
        user_id: str | None = None,
        started_from: str | None = None,
        started_to: str | None = None,
    ) -> AnalysisAgentTraceDegradeTopNResponse:
        from_dt = _parse_iso8601(started_from)
        to_dt = _parse_iso8601(started_to)
        items, _ = self._trace_store.list(
            limit=5000,
            offset=0,
            score_min_ms=int(from_dt.timestamp() * 1000) if from_dt else None,
            score_max_ms=int(to_dt.timestamp() * 1000) if to_dt else None,
            analysis_type=analysis_type,
            user_id=user_id,
        )
        counts: dict[str, int] = {}
        for x in items:
            reasons = list(x.get("degrade_reasons") or [])
            if not reasons:
                tr = x.get("trace") if isinstance(x.get("trace"), dict) else {}
                reasons = list(tr.get("degrade_reasons") or [])
            for reason in reasons:
                if not reason:
                    continue
                counts[str(reason)] = counts.get(str(reason), 0) + 1
        rows = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        limit_n = max(1, min(top_n, 50))
        return AnalysisAgentTraceDegradeTopNResponse(
            ok=True,
            total_unique=len(rows),
            items=[AnalysisAgentTraceDegradeItem(reason=k, count=v) for k, v in rows[:limit_n]],
        )

    async def run_stream(self, data: AnalysisAgentRunRequest) -> StreamingResponse:
        opts = data.options.model_dump()

        async def on_complete(result: dict[str, Any]) -> None:
            self._save_trace(result)

        async def gen():
            async for ev in self._runner_or_create.iter_stream_events(
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
            async for ev in self._runner_or_create.iter_resume_stream_events(
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
        result = await self._runner_or_create.resume_to_result(
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
