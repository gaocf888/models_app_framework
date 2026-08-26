"""T6：analysis_agent Trace store + list/stats/trend/degrade-topn。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.services.analysis_agent_service import AnalysisAgentService
from app.services.analysis_agent_trace_store import (
    InMemoryAnalysisAgentTraceStore,
    create_analysis_agent_trace_store,
)


def _sample(
    rid: str,
    *,
    analysis_type: str = "overheat_guidance",
    user_id: str = "u1",
    status: str = "success",
    degrade: list[str] | None = None,
    started_at: str | None = None,
) -> dict:
    now = started_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "request_id": rid,
        "analysis_type": analysis_type,
        "user_id": user_id,
        "session_id": "s1",
        "summary": f"summary for {rid}",
        "structured_report": {},
        "evidence": {},
        "degrade_reasons": list(degrade or []),
        "started_at": now,
        "finished_at": now,
        "total_latency_ms": 12,
        "trace": {"status": status, "degrade_reasons": list(degrade or [])},
    }


def test_memory_store_save_get_list() -> None:
    store = InMemoryAnalysisAgentTraceStore(max_items=100)
    store.save(_sample("aa_1", analysis_type="subsidence_quarterly", user_id="alice"))
    store.save(_sample("aa_2", analysis_type="overheat_guidance", user_id="bob"))
    hit = store.get("aa_1")
    assert hit is not None
    assert hit["analysis_type"] == "subsidence_quarterly"
    items, total = store.list(10, 0, analysis_type="subsidence_quarterly")
    assert total == 1
    assert items[0]["request_id"] == "aa_1"
    items2, total2 = store.list(10, 0, user_id="bob")
    assert total2 == 1
    assert items2[0]["request_id"] == "aa_2"


def test_create_store_defaults_memory() -> None:
    store = create_analysis_agent_trace_store(backend="memory", max_items=50)
    assert isinstance(store, InMemoryAnalysisAgentTraceStore)


def test_service_list_stats_trend_degrade() -> None:
    store = InMemoryAnalysisAgentTraceStore(max_items=100)
    svc = AnalysisAgentService(trace_store=store)
    t0 = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    svc._save_trace(
        _sample(
            "t1",
            analysis_type="overheat_guidance",
            degrade=["mandatory_empty_continue"],
            started_at=t0.isoformat().replace("+00:00", "Z"),
        )
    )
    svc._save_trace(
        _sample(
            "t2",
            analysis_type="subsidence_quarterly",
            user_id="u2",
            status="aborted",
            degrade=["mandatory_empty_continue", "quality_l1_missing_anchor"],
            started_at=(t0 + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        )
    )
    svc._save_trace(
        _sample(
            "t3",
            analysis_type="overheat_guidance",
            started_at=(t0 + timedelta(hours=1, minutes=5)).isoformat().replace("+00:00", "Z"),
        )
    )

    items, total = svc.list_traces(limit=10, offset=0)
    assert total == 3
    assert len(items) == 3

    filtered, ftotal = svc.list_traces(limit=10, offset=0, analysis_type="overheat_guidance")
    assert ftotal == 2
    assert all(i.analysis_type == "overheat_guidance" for i in filtered)

    like_items, like_total = svc.list_traces(limit=10, offset=0, request_id_like="t2")
    assert like_total == 1
    assert like_items[0].request_id == "t2"

    stats = svc.get_trace_stats()
    assert stats.total == 3
    assert stats.by_analysis_type.get("overheat_guidance") == 2
    assert stats.by_status.get("aborted") == 1
    assert stats.degrade_reasons.get("mandatory_empty_continue") == 2

    trend = svc.get_trace_trend(bucket="hour")
    assert trend.ok
    assert trend.bucket == "hour"
    assert sum(p.total for p in trend.points) == 3

    top = svc.get_degrade_topn(top_n=5)
    assert top.total_unique >= 1
    assert top.items[0].reason == "mandatory_empty_continue"
    assert top.items[0].count == 2

    detail = svc.get_trace("t1")
    assert detail is not None
    assert detail["request_id"] == "t1"
    assert svc.get_trace("missing") is None


def test_api_traces_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InMemoryAnalysisAgentTraceStore(max_items=100)
    svc = AnalysisAgentService(trace_store=store)
    svc._save_trace(_sample("api_1", degrade=["x"]))

    import app.api.analysis_agent as api_mod

    monkeypatch.setattr(api_mod, "service", svc)

    # 避免整应用依赖：直接挂 router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(api_mod.router, prefix="/analysis-agent")
    client = TestClient(app)

    r = client.get("/analysis-agent/traces", params={"limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["request_id"] == "api_1"

    r2 = client.get("/analysis-agent/traces/stats")
    assert r2.status_code == 200
    assert r2.json()["total"] == 1

    r3 = client.get("/analysis-agent/traces/trend", params={"bucket": "hour"})
    assert r3.status_code == 200
    assert r3.json()["ok"] is True

    r4 = client.get("/analysis-agent/traces/degrade-topn", params={"top_n": 3})
    assert r4.status_code == 200
    assert r4.json()["items"][0]["reason"] == "x"

    r5 = client.get("/analysis-agent/trace/api_1")
    assert r5.status_code == 200
    assert r5.json()["request_id"] == "api_1"

    r6 = client.get("/analysis-agent/traces/api_1")
    assert r6.status_code == 200
    assert r6.json()["request_id"] == "api_1"

    r7 = client.get("/analysis-agent/trace/nope")
    assert r7.status_code == 404
