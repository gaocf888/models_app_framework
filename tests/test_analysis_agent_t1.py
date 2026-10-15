"""T1：全量 acquire_data + 去 HITL 质量门。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis_agent.graph.builder import build_analysis_agent_graph, _route_after_quality
from app.analysis_agent.graph.nodes import make_nodes
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


def _event_names(update: dict) -> list[str]:
    return [str(e.get("event") or "") for e in (update.get("pending_events") or [])]


@pytest.mark.asyncio
async def test_graph_nodes_return_incremental_pending_events() -> None:
    """节点发出后只返回本节点增量，避免 leftover pending 被 updates 重放成多条 meta / 多组 nl2sql_done。"""
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(return_value=NL2SQLQueryResponse(sql="SELECT 1", rows=[{"v": 1}]))
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=nl2sql)
    nodes = make_nodes(orch)
    graph_state: AnalysisAgentState = {
        "request_id": "aa_inc",
        "user_id": "u1",
        "session_id": "s1",
        "analysis_type": "overheat_guidance",
        "query": "分析",
        "options": {
            "enable_rag": False,
            "plan_template_version": get_default_agent_template_version(),
        },
        "pending_events": [{"event": "stale_should_not_leak"}],
    }

    init_out = await nodes["initialize"](graph_state)
    graph_state.update(init_out)
    assert _event_names(init_out) == ["analysis_agent_meta"]
    assert "stale_should_not_leak" not in _event_names(init_out)
    assert "ordered_slots" in init_out
    assert "gathered_data" in init_out

    rag_out = nodes["intent_rag"](graph_state)
    graph_state.update(rag_out)
    assert rag_out.get("pending_events") == []
    assert set(rag_out.keys()) <= {"pending_events", "context_snippets", "intent_context"}

    leftover = [
        {"event": "analysis_agent_meta", "request_id": "aa_inc"},
        {"event": "analysis_agent_nl2sql_done", "item_id": "old"},
    ]
    graph_state["pending_events"] = list(leftover)
    acq_out = await nodes["acquire_data"](graph_state)
    graph_state.update(acq_out)
    acq_names = _event_names(acq_out)
    assert "analysis_agent_meta" not in acq_names
    assert "old" not in [e.get("item_id") for e in (acq_out.get("pending_events") or [])]
    assert "analysis_agent_nl2sql_done" in acq_names

    graph_state["pending_events"] = list(acq_out.get("pending_events") or [])
    dq_out = nodes["data_quality"](graph_state)
    graph_state.update(dq_out)
    assert dq_out.get("pending_events") == []
    assert "analysis_agent_nl2sql_done" not in _event_names(dq_out)

    graph_state["ordered_slots"] = []
    graph_state["slots_total"] = 0
    graph_state["pending_events"] = list(acq_out.get("pending_events") or [])
    ch_out = await nodes["chapter_pipeline"](graph_state)
    graph_state.update(ch_out)
    assert "analysis_agent_nl2sql_done" not in _event_names(ch_out)
    assert "analysis_agent_meta" not in _event_names(ch_out)

    graph_state["pending_events"] = list(acq_out.get("pending_events") or [])
    fin_out = nodes["finalize"](graph_state)
    fin_names = _event_names(fin_out)
    assert "analysis_agent_nl2sql_done" not in fin_names
    assert "analysis_agent_report_complete" in fin_names
    assert "analysis_agent_finished" in fin_names
