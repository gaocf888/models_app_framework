"""T1：全量 acquire_data + 去 HITL 质量门。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis_agent.graph.builder import build_analysis_agent_graph, _route_after_quality
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.slots.specs import get_default_agent_template_version
from app.models.nl2sql import NL2SQLQueryResponse


def test_route_after_quality_retry_and_abort() -> None:
    assert _route_after_quality({"abort_requested": True}) == "finalize"
    assert _route_after_quality({"acquire_retry": True}) == "acquire_data"
    assert _route_after_quality({}) == "chapter_pipeline"


def test_graph_has_acquire_data_no_human() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    graph, _cp = build_analysis_agent_graph(orch)
    if graph is None:
        pytest.skip("langgraph not available")
    # compiled graph node set
    nodes = set(graph.get_graph().nodes.keys()) if hasattr(graph, "get_graph") else set()
    if nodes:
        assert "acquire_data" in nodes
        assert "data_quality" in nodes
        assert "chapter_pipeline" in nodes
        assert "slot_human" not in nodes
        assert "slot_nl2sql" not in nodes
        assert "slot_prepare" not in nodes


def test_data_quality_retries_then_degrades() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    with patch.object(orch._cfg, "acquire_max_retries", 1):
        state: AnalysisAgentState = {
            "plan_tasks": [{"item_id": "q1", "mandatory": True}],
            "task_status": {"q1": "mandatory_empty"},
            "gathered_data": {},
            "options": {"strict": False},
            "_acquire_retries": 0,
            "degrade_reasons": [],
        }
        out = orch.run_data_quality(state)
        assert out.get("acquire_retry") is True
        assert "q1" not in (out.get("task_status") or {})

        state2: AnalysisAgentState = {
            "plan_tasks": [{"item_id": "q1", "mandatory": True}],
            "task_status": {"q1": "mandatory_empty"},
            "gathered_data": {},
            "options": {"strict": False},
            "_acquire_retries": 1,
            "degrade_reasons": [],
        }
        out2 = orch.run_data_quality(state2)
        assert out2.get("acquire_retry") is not True
        assert out2.get("abort_requested") is not True
        assert "mandatory_empty_continue" in (out2.get("degrade_reasons") or [])


def test_data_quality_strict_aborts() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    with patch.object(orch._cfg, "acquire_max_retries", 0):
        state: AnalysisAgentState = {
            "plan_tasks": [{"item_id": "q1", "mandatory": True}],
            "task_status": {"q1": "mandatory_failed"},
            "gathered_data": {},
            "options": {"strict": True},
            "_acquire_retries": 0,
            "degrade_reasons": [],
        }
        out = orch.run_data_quality(state)
        assert out.get("abort_requested") is True
        assert out.get("error")


@pytest.mark.asyncio
async def test_acquire_all_plan_dedupes_and_layers() -> None:
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(
        side_effect=[
            NL2SQLQueryResponse(sql="SELECT 1", rows=[{"v": 1}]),
            NL2SQLQueryResponse(sql="SELECT 2", rows=[{"v": 2}]),
        ]
    )
    orch = SlotOrchestrator(nl2sql_service=nl2sql, hybrid_rag=MagicMock())
    default_ver = get_default_agent_template_version()
    state: AnalysisAgentState = {
        "user_id": "u1",
        "session_id": "s1",
        "request_id": "aa_t1",
        "analysis_type": "maintenance_strategy",
        "query": "分析",
        "options": {"plan_template_version": default_ver, "max_rows_per_query": 100},
        "trace": {"plan_template_version": default_ver},
        "plan_tasks": [
            {"item_id": "q1", "question": "q1", "mandatory": True, "dependency_ids": []},
            {"item_id": "q1", "question": "dup", "mandatory": True, "dependency_ids": []},
            {"item_id": "q2", "question": "q2", "mandatory": False, "dependency_ids": ["q1"]},
        ],
        "gathered_data": {},
        "task_status": {},
        "nl2sql_calls": [],
        "pending_events": [],
    }
    await orch.run_acquire_data(state)
    assert nl2sql.query.await_count == 2
    assert set(state["gathered_data"].keys()) == {"q1", "q2"}
    assert state["task_status"]["q1"] == "success"
    assert state["task_status"]["q2"] == "success"
    # 第二次全量调用应全部 cache
    nl2sql.query.reset_mock()
    await orch.run_acquire_data(state)
    assert nl2sql.query.await_count == 0
    assert any(c.get("cache_hit") for c in state["nl2sql_calls"])


@pytest.mark.asyncio
async def test_acquire_skips_when_dependency_failed() -> None:
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(side_effect=RuntimeError("db down"))
    orch = SlotOrchestrator(nl2sql_service=nl2sql, hybrid_rag=MagicMock())
    default_ver = get_default_agent_template_version()
    state: AnalysisAgentState = {
        "user_id": "u1",
        "session_id": "s1",
        "request_id": "aa_t1b",
        "analysis_type": "maintenance_strategy",
        "query": "分析",
        "options": {"plan_template_version": default_ver},
        "trace": {"plan_template_version": default_ver},
        "plan_tasks": [
            {"item_id": "q1", "question": "q1", "mandatory": True, "dependency_ids": []},
            {"item_id": "q2", "question": "q2", "mandatory": False, "dependency_ids": ["q1"]},
        ],
        "gathered_data": {},
        "task_status": {},
        "nl2sql_calls": [],
        "pending_events": [],
    }
    await orch.run_acquire_data(state)
    assert state["task_status"]["q1"] == "mandatory_failed"
    assert state["task_status"]["q2"] in ("optional_failed", "mandatory_failed")
    # q2 不应再发起 query（依赖失败 skipped）
    assert nl2sql.query.await_count == 1
