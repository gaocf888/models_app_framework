from __future__ import annotations

"""统一 ExecutionTrace / TraceRecorder / ops API 基础单测。"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# 强制 memory + 关闭 OTLP，避免环境干扰
os.environ["EXECUTION_TRACE_ENABLED"] = "true"
os.environ["EXECUTION_TRACE_BACKEND"] = "memory"
os.environ["EXECUTION_TRACE_OTLP_ENABLED"] = "false"
os.environ["LANGSMITH_ENABLED"] = "false"


@pytest.fixture(autouse=True)
def _reset_store():
    from app.observability.settings import get_execution_trace_settings
    from app.services.execution_trace_store import reset_execution_trace_store_for_tests

    get_execution_trace_settings.cache_clear()
    reset_execution_trace_store_for_tests()
    yield
    get_execution_trace_settings.cache_clear()
    reset_execution_trace_store_for_tests()


def test_trace_recorder_finalize_and_get():
    from app.observability.trace_recorder import TraceRecorder
    from app.services.execution_trace_store import get_execution_trace_store

    tr = TraceRecorder.start(module="chatbot", request_id="rid-1", scene="kb_qa", kind="request")
    with tr.node("intent"):
        pass
    tr.record_node("answer", latency_ms=12)
    tr.add_degrade("demo")
    rec = tr.finalize(status="success", summary="ok")
    assert rec is not None
    assert rec.request_id == "rid-1"
    assert len(rec.nodes) >= 2
    hit = get_execution_trace_store().get("rid-1")
    assert hit is not None
    assert hit.degrade_reasons == ["demo"]


def test_sanitizer_redacts_secrets():
    from app.models.execution_trace import ExecutionTraceRecord
    from app.observability.sanitizer import sanitize_record

    raw = ExecutionTraceRecord(
        request_id="x",
        module="nl2sql",
        started_at="2026-01-01T00:00:00+00:00",
        meta={"api_key": "secret-value", "sql": "select 1"},
    )
    clean = sanitize_record(raw)
    assert clean.meta["api_key"] == "***"


def test_ops_traces_api():
    from app.api import ops_traces
    from app.observability.trace_recorder import TraceRecorder

    TraceRecorder.start(module="llm_infer", request_id="ops-1").finalize(status="success")

    app = FastAPI()
    app.include_router(ops_traces.router, prefix="/ops")
    client = TestClient(app)
    r = client.get("/ops/traces/ops-1")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["trace"]["request_id"] == "ops-1"

    listed = client.get("/ops/traces", params={"module": "llm_infer"})
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1

    stats = client.get("/ops/traces/stats")
    assert stats.status_code == 200
    assert stats.json()["total"] >= 1


def test_analysis_projector():
    from app.models.analysis import AnalysisEvidence, AnalysisTrace, AnalysisV2Result
    from app.observability.analysis_projector import project_analysis_result
    from app.services.execution_trace_store import get_execution_trace_store

    result = AnalysisV2Result(
        request_id="an-1",
        analysis_type="custom",
        summary="hello",
        structured_report={},
        evidence=AnalysisEvidence(),
        trace=AnalysisTrace(
            plan_id="p1",
            node_latency_ms={"plan": 10, "answer": 20},
            node_status={"plan": "success", "answer": "success"},
            degrade_reasons=["d1"],
        ),
    )
    project_analysis_result(result)
    hit = get_execution_trace_store().get("an-1")
    assert hit is not None
    assert hit.module == "analysis"
    assert len(hit.nodes) == 2


def test_otlp_payload_shape():
    from app.models.execution_trace import ExecutionTraceRecord, TraceNode
    from app.observability.otlp_exporter import record_to_otlp_payload

    rec = ExecutionTraceRecord(
        request_id="rid",
        module="chatbot",
        kind="request",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        total_latency_ms=1000,
        nodes=[TraceNode(node_id="intent", latency_ms=5, status="success")],
        status="success",
    )
    payload = record_to_otlp_payload(rec, service_name="models-app")
    assert "resourceSpans" in payload
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert len(spans) == 2
    assert spans[0]["name"].endswith(".request")


def test_meta_patch_writeback():
    from app.observability.meta_patch import patch_execution_trace_meta
    from app.observability.trace_recorder import TraceRecorder
    from app.services.execution_trace_store import get_execution_trace_store

    TraceRecorder.start(module="chatbot", request_id="meta-1").finalize(status="success")
    patch_execution_trace_meta("meta-1", {"tempo_trace_id": "a" * 32, "langsmith_run_id": "ls-1"})
    hit = get_execution_trace_store().get("meta-1")
    assert hit is not None
    assert hit.meta.get("tempo_trace_id") == "a" * 32
    assert hit.meta.get("langsmith_run_id") == "ls-1"


def test_list_time_window_and_result_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import ops_traces
    from app.observability.trace_recorder import TraceRecorder

    TraceRecorder.start(module="nl2sql", request_id="tw-1").finalize(status="success")
    app = FastAPI()
    app.include_router(ops_traces.router, prefix="/ops")
    client = TestClient(app)
    listed = client.get("/ops/traces", params={"module": "nl2sql", "started_after": "2000-01-01T00:00:00+00:00"})
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    empty = client.get("/ops/traces", params={"started_before": "2000-01-01T00:00:00+00:00"})
    assert empty.status_code == 200
    res = client.get("/ops/traces/tw-1/result")
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_traces_status_endpoint():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import ops_traces

    app = FastAPI()
    app.include_router(ops_traces.router, prefix="/ops")
    client = TestClient(app)
    r = client.get("/ops/traces-status")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["recommended_path"] == "redis+tempo"
    assert body["backend_impl"] == "InMemoryExecutionTraceStore"
    assert body["otlp_enabled"] is False


def test_backend_auto_prefers_redis_when_url_set(monkeypatch):
    from app.observability.settings import get_execution_trace_settings

    monkeypatch.delenv("EXECUTION_TRACE_BACKEND", raising=False)
    monkeypatch.delenv("ANALYSIS_TRACE_BACKEND", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    get_execution_trace_settings.cache_clear()
    cfg = get_execution_trace_settings()
    assert cfg.backend == "redis"
    assert "monitoring-tempo" in cfg.otlp_endpoint


def test_backend_es_falls_back(monkeypatch):
    from app.observability.settings import get_execution_trace_settings

    monkeypatch.setenv("EXECUTION_TRACE_BACKEND", "es")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    get_execution_trace_settings.cache_clear()
    assert get_execution_trace_settings().backend == "redis"


def test_preassign_tempo_trace_id_on_finalize(monkeypatch):
    from app.observability.settings import get_execution_trace_settings
    from app.observability.trace_recorder import TraceRecorder
    from app.services.execution_trace_store import get_execution_trace_store

    monkeypatch.setenv("EXECUTION_TRACE_OTLP_ENABLED", "true")
    monkeypatch.setenv("OTEL_PREASSIGN_TRACE_ID", "true")
    get_execution_trace_settings.cache_clear()

    TraceRecorder.start(module="chatbot", request_id="tempo-pre-1").finalize(
        status="success", export_otlp=False
    )
    hit = get_execution_trace_store().get("tempo-pre-1")
    assert hit is not None
    tid = hit.meta.get("tempo_trace_id")
    assert isinstance(tid, str) and len(tid) == 32
