from __future__ import annotations

"""将 ExecutionTraceRecord 导出为 OTLP/HTTP JSON（可选；失败 no-op）。

说明：默认使用 OTLP HTTP JSON（Content-Type: application/json）。
环境变量 `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf` 在本实现中仍走 JSON
（Tempo 2.x 同时接受 JSON）；若需严格 protobuf 可后续换 OpenTelemetry SDK。
"""

import json
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib import request as urlrequest

from app.core.logging import get_logger
from app.models.execution_trace import ExecutionTraceRecord
from app.observability.sanitizer import sanitize_record
from app.observability.settings import get_execution_trace_settings

logger = get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="otlp-export")


def _parse_iso_ns(value: Optional[str], fallback_ns: int) -> int:
    if not value:
        return fallback_ns
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1_000_000_000)
    except Exception:  # noqa: BLE001
        return fallback_ns


def _hex_id(n_bytes: int) -> str:
    return uuid.uuid4().hex[: n_bytes * 2]


def ensure_tempo_trace_id(record: ExecutionTraceRecord) -> str:
    """保证 record.meta.tempo_trace_id 为 32 位 hex（W3C）；便于先写入 Redis 再导出 Tempo。"""
    meta = dict(record.meta or {})
    tid = str(meta.get("tempo_trace_id") or "")
    if len(tid) != 32 or any(c not in "0123456789abcdef" for c in tid.lower()):
        tid = _hex_id(16)
        meta["tempo_trace_id"] = tid
        record.meta = meta
    else:
        meta["tempo_trace_id"] = tid.lower()
        record.meta = meta
        tid = tid.lower()
    return tid


def maybe_preassign_tempo_trace_id(record: ExecutionTraceRecord) -> ExecutionTraceRecord:
    """OTLP 开启且允许预写时，在落 Redis 前写入 tempo_trace_id。"""
    cfg = get_execution_trace_settings()
    if not cfg.otlp_enabled or not cfg.otlp_preassign_trace_id:
        return record
    if record.module not in cfg.otlp_modules:
        return record
    ensure_tempo_trace_id(record)
    return record


def record_to_otlp_payload(record: ExecutionTraceRecord, *, service_name: str) -> Dict[str, Any]:
    """构造 OTLP/HTTP JSON traces 载荷（无需 protobuf）。"""
    now_ns = int(time.time() * 1_000_000_000)
    start_ns = _parse_iso_ns(record.started_at, now_ns)
    end_ns = _parse_iso_ns(record.finished_at, start_ns + int((record.total_latency_ms or 0) * 1_000_000))
    trace_id = ensure_tempo_trace_id(record)
    root_span_id = _hex_id(8)
    root_name = f"{record.module}.{'job' if record.kind == 'job' else 'request'}"
    root_attrs = [
        {"key": "request_id", "value": {"stringValue": record.request_id}},
        {"key": "kind", "value": {"stringValue": record.kind}},
        {"key": "module", "value": {"stringValue": record.module}},
        {"key": "status", "value": {"stringValue": record.status}},
    ]
    if record.kind == "job":
        root_attrs.append({"key": "job_id", "value": {"stringValue": record.request_id}})
    if record.scene:
        root_attrs.append({"key": "scene", "value": {"stringValue": record.scene}})
    for i, reason in enumerate((record.degrade_reasons or [])[:10]):
        root_attrs.append({"key": f"degrade.{i}", "value": {"stringValue": str(reason)}})

    spans: list[dict[str, Any]] = [
        {
            "traceId": trace_id,
            "spanId": root_span_id,
            "name": root_name,
            "kind": 1,
            "startTimeUnixNano": str(start_ns),
            "endTimeUnixNano": str(end_ns),
            "attributes": root_attrs,
            "status": {
                "code": 2 if record.status in {"failed", "aborted"} else 1,
                "message": record.status,
            },
        }
    ]
    cursor = start_ns
    for node in record.nodes or []:
        child_id = _hex_id(8)
        n_start = _parse_iso_ns(node.started_at, cursor)
        dur = int((node.latency_ms or 0) * 1_000_000)
        n_end = _parse_iso_ns(node.finished_at, n_start + max(dur, 1))
        cursor = n_end
        child_attrs = [
            {"key": "request_id", "value": {"stringValue": record.request_id}},
            {"key": "node_id", "value": {"stringValue": node.node_id}},
            {"key": "node_status", "value": {"stringValue": node.status}},
        ]
        spans.append(
            {
                "traceId": trace_id,
                "spanId": child_id,
                "parentSpanId": root_span_id,
                "name": node.node_id,
                "kind": 1,
                "startTimeUnixNano": str(n_start),
                "endTimeUnixNano": str(n_end),
                "attributes": child_attrs,
                "status": {
                    "code": 2 if node.status == "failed" else 1,
                    "message": node.error or node.status,
                },
            }
        )

    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": service_name}},
                    ]
                },
                "scopeSpans": [{"scope": {"name": "models-app.execution-trace"}, "spans": spans}],
            }
        ],
        "_meta": {"trace_id": trace_id},
    }


class OtlpTraceExporter:
    def __init__(self) -> None:
        self._lock = threading.Lock()

    def export_async(self, record: ExecutionTraceRecord, *, live: bool = False) -> None:
        cfg = get_execution_trace_settings()
        if not cfg.otlp_enabled:
            return
        if record.module not in cfg.otlp_modules:
            return
        if live and record.kind == "job" and not cfg.otlp_job_live_export:
            return
        if random.random() > cfg.otlp_sample_rate:
            self._metric(record.module, "skipped")
            return
        try:
            _executor.submit(self._export_sync, record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTLP schedule failed: %s", exc)
            self._metric(record.module, "error")

    def _export_sync(self, record: ExecutionTraceRecord) -> None:
        started = time.perf_counter()
        cfg = get_execution_trace_settings()
        try:
            clean = sanitize_record(record)
            payload = record_to_otlp_payload(clean, service_name=cfg.otlp_service_name)
            trace_id = payload.pop("_meta", {}).get("trace_id")
            body = json.dumps(payload).encode("utf-8")
            url = f"{cfg.otlp_endpoint}/v1/traces"
            req = urlrequest.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlrequest.urlopen(req, timeout=3) as resp:  # noqa: S310 - ops endpoint
                _ = resp.read()
            if trace_id:
                record.meta = dict(record.meta or {})
                record.meta["tempo_trace_id"] = trace_id
                try:
                    from app.observability.meta_patch import patch_execution_trace_meta

                    patch_execution_trace_meta(record.request_id, {"tempo_trace_id": trace_id})
                except Exception:  # noqa: BLE001
                    pass
            self._metric(record.module, "ok")
        except Exception as exc:  # noqa: BLE001
            logger.warning("OTLP export failed module=%s: %s", record.module, exc)
            self._metric(record.module, "error")
        finally:
            try:
                from app.core.metrics import OTLP_EXPORT_LATENCY

                OTLP_EXPORT_LATENCY.labels(module=record.module).observe(time.perf_counter() - started)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _metric(module: str, result: str) -> None:
        try:
            from app.core.metrics import OTLP_EXPORT_TOTAL

            OTLP_EXPORT_TOTAL.labels(module=module, result=result).inc()
        except Exception:  # noqa: BLE001
            pass


_exporter: OtlpTraceExporter | None = None


def get_otlp_exporter() -> OtlpTraceExporter:
    global _exporter
    if _exporter is None:
        _exporter = OtlpTraceExporter()
    return _exporter
