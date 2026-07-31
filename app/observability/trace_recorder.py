from __future__ import annotations

"""统一埋点 Facade：不改变业务逻辑，仅记录节点/阶段并旁路落库。"""

import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from app.core.logging import get_logger
from app.models.execution_trace import ExecutionTraceRecord, TraceKind, TraceNode, TraceStatus
from app.observability.otlp_exporter import get_otlp_exporter
from app.observability.sanitizer import sanitize_record
from app.observability.settings import get_execution_trace_settings, module_enabled
from app.services.execution_trace_store import get_execution_trace_store

logger = get_logger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TraceRecorder:
    """请求/任务级轨迹记录器。"""

    def __init__(
        self,
        *,
        module: str,
        request_id: str | None = None,
        kind: TraceKind = "request",
        scene: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        meta: Dict[str, Any] | None = None,
    ) -> None:
        self.enabled = module_enabled(module)
        self.module = module
        self.request_id = request_id or str(uuid.uuid4())
        self.kind: TraceKind = kind
        self.scene = scene
        self.user_id = user_id
        self.session_id = session_id
        self.meta: Dict[str, Any] = dict(meta or {})
        if kind == "job":
            self.meta.setdefault("job_id", self.request_id)
        self.degrade_reasons: List[str] = []
        self.nodes: List[TraceNode] = []
        self.summary: str | None = None
        self.status: TraceStatus = "running"
        self.started_at = _iso_now()
        self.finished_at: str | None = None
        self._t0 = time.perf_counter()
        self._node_stack: list[tuple[str, float, Dict[str, Any]]] = []

    @classmethod
    def start(
        cls,
        *,
        module: str,
        request_id: str | None = None,
        kind: TraceKind = "request",
        scene: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        meta: Dict[str, Any] | None = None,
    ) -> "TraceRecorder":
        return cls(
            module=module,
            request_id=request_id,
            kind=kind,
            scene=scene,
            user_id=user_id,
            session_id=session_id,
            meta=meta,
        )

    def add_degrade(self, reason: str) -> None:
        if reason and reason not in self.degrade_reasons:
            self.degrade_reasons.append(reason)

    def set_summary(self, summary: str | None) -> None:
        self.summary = summary

    def record_node(
        self,
        node_id: str,
        *,
        status: str = "success",
        latency_ms: int | None = None,
        error: str | None = None,
        attributes: Dict[str, Any] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        self.nodes.append(
            TraceNode(
                node_id=node_id,
                status=status,  # type: ignore[arg-type]
                latency_ms=latency_ms,
                started_at=started_at,
                finished_at=finished_at or _iso_now(),
                error=error,
                attributes=dict(attributes or {}),
            )
        )

    @contextmanager
    def node(self, node_id: str, attributes: Dict[str, Any] | None = None) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        started = _iso_now()
        attrs = dict(attributes or {})
        err: str | None = None
        status = "success"
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            status = "failed"
            raise
        finally:
            self.record_node(
                node_id,
                status=status,
                latency_ms=int((time.perf_counter() - t0) * 1000),
                error=err,
                attributes=attrs,
                started_at=started,
                finished_at=_iso_now(),
            )

    def build_record(self, *, status: TraceStatus | None = None) -> ExecutionTraceRecord:
        st: TraceStatus = status or self.status
        finished = self.finished_at
        total_ms = None
        if st != "running":
            finished = finished or _iso_now()
            total_ms = int((time.perf_counter() - self._t0) * 1000)
        return ExecutionTraceRecord(
            request_id=self.request_id,
            kind=self.kind,
            module=self.module,
            scene=self.scene,
            user_id=self.user_id,
            session_id=self.session_id,
            status=st,
            started_at=self.started_at,
            finished_at=finished,
            total_latency_ms=total_ms,
            nodes=list(self.nodes),
            degrade_reasons=list(self.degrade_reasons),
            summary=self.summary,
            meta=dict(self.meta),
        )

    def checkpoint(self) -> None:
        """任务进行中覆盖写 Store（不导出 OTLP，除非配置 live）。"""
        if not self.enabled:
            return
        try:
            record = sanitize_record(self.build_record(status="running"))
            get_execution_trace_store().save(record)
            self._metric_saved(record, checkpoint=True)
            if self.kind == "job":
                get_otlp_exporter().export_async(record, live=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TraceRecorder.checkpoint failed: %s", exc)
            self._metric_error()

    def finalize(
        self,
        *,
        status: TraceStatus = "success",
        summary: str | None = None,
        export_otlp: bool = True,
        mirror_langsmith: bool = True,
    ) -> ExecutionTraceRecord | None:
        if not self.enabled:
            return None
        if summary is not None:
            self.summary = summary
        self.status = status
        self.finished_at = _iso_now()
        record = sanitize_record(self.build_record(status=status))
        try:
            from app.observability.otlp_exporter import maybe_preassign_tempo_trace_id

            record = maybe_preassign_tempo_trace_id(record)
            get_execution_trace_store().save(record)
            self._metric_saved(record, checkpoint=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("TraceRecorder.finalize save failed: %s", exc)
            self._metric_error()
            return record

        if export_otlp:
            try:
                get_otlp_exporter().export_async(record, live=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TraceRecorder OTLP failed: %s", exc)

        if mirror_langsmith:
            try:
                from app.llm.langsmith_tracker import get_langsmith_tracker

                get_langsmith_tracker().mirror_execution_trace(record)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TraceRecorder LangSmith mirror failed: %s", exc)
        return record

    @staticmethod
    def _metric_saved(record: ExecutionTraceRecord, *, checkpoint: bool) -> None:
        try:
            from app.core.metrics import (
                EXECUTION_TRACE_CHECKPOINT_TOTAL,
                EXECUTION_TRACE_SAVED_TOTAL,
            )

            EXECUTION_TRACE_SAVED_TOTAL.labels(
                module=record.module, kind=record.kind, status=record.status
            ).inc()
            if checkpoint:
                EXECUTION_TRACE_CHECKPOINT_TOTAL.labels(module=record.module).inc()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _metric_error() -> None:
        try:
            from app.core.metrics import EXECUTION_TRACE_SAVE_ERRORS_TOTAL

            EXECUTION_TRACE_SAVE_ERRORS_TOTAL.labels(module="unknown", backend="store").inc()
        except Exception:  # noqa: BLE001
            pass


def save_execution_trace_record(record: ExecutionTraceRecord, *, finalize_side_effects: bool = True) -> None:
    """直接保存已构造的 record（用于 analysis 投影等）。"""
    if not module_enabled(record.module):
        return
    try:
        from app.observability.otlp_exporter import maybe_preassign_tempo_trace_id

        clean = maybe_preassign_tempo_trace_id(sanitize_record(record))
        get_execution_trace_store().save(clean)
        TraceRecorder._metric_saved(clean, checkpoint=False)
        if finalize_side_effects:
            get_otlp_exporter().export_async(clean, live=False)
            try:
                from app.llm.langsmith_tracker import get_langsmith_tracker

                get_langsmith_tracker().mirror_execution_trace(clean)
            except Exception:  # noqa: BLE001
                pass
    except Exception as exc:  # noqa: BLE001
        logger.warning("save_execution_trace_record failed: %s", exc)
        TraceRecorder._metric_error()
