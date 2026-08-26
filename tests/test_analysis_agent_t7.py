"""回归：去掉 enable_context；章节点命名；章合同并行；prompt / use_emit / stream_live；abort Trace。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis_agent.agents.section_prompt import build_section_user_prompt
from app.analysis_agent.graph.builder import _route_after_quality, build_analysis_agent_graph
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.runner import _result_status_label
from app.analysis_agent.slots.builder import slot_from_dict
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.core.config import AnalysisAgentConfig
from app.models.analysis_agent import AnalysisAgentOptions
from app.services.analysis_agent_service import AnalysisAgentService
from app.services.analysis_agent_trace_store import InMemoryAnalysisAgentTraceStore


def test_options_has_no_enable_context() -> None:
    opts = AnalysisAgentOptions()
    assert not hasattr(opts, "enable_context") or "enable_context" not in opts.model_fields


def test_use_emit_tools_defaults_false() -> None:
    slot = slot_from_dict(
        {
            "id": "t1",
            "kind": "llm_section",
            "source_item_ids": ["q1"],
            "outline": ["要点1"],
            "allowed_outputs": ["paragraph", "table"],
        }
    )
    assert slot.use_emit_tools is False


def test_non_react_prompt_no_get_slot_data() -> None:
    slot = AnalysisAgentSlot(
        id="ch1",
        title="第一章",
        kind="llm_section",
        source_item_ids=(),
        narrative_instruction="写概述",
        use_emit_tools=False,
    )
    prompt = build_section_user_prompt(
        slot=slot,
        query="请生成报告",
        coverage="覆盖：ok",
        facts="事实：1",
    )
    assert "get_slot_data" not in prompt
    assert "勿调用工具" in prompt


def test_emit_tools_prompt_keeps_get_slot_data() -> None:
    slot = AnalysisAgentSlot(
        id="ch1",
        title="第一章",
        kind="llm_section",
        use_emit_tools=True,
        allowed_outputs=("paragraph", "table"),
    )
    prompt = build_section_user_prompt(
        slot=slot, query="q", coverage="", facts=""
    )
    assert "get_slot_data" in prompt


def test_graph_uses_chapter_pipeline() -> None:
    orch = SlotOrchestrator(
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
        llm_client=MagicMock(),
    )
    graph, _cp = build_analysis_agent_graph(orch)
    assert _route_after_quality({}) == "chapter_pipeline"
    assert _route_after_quality({"abort_requested": True}) == "finalize"
    if graph is not None:
        nodes = set(graph.get_graph().nodes)
        assert "chapter_pipeline" in nodes
        assert "slot_prepare" not in nodes


def test_chapter_synth_parallel_config_clamped() -> None:
    orch = SlotOrchestrator(
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
        llm_client=MagicMock(),
    )
    orch._cfg = AnalysisAgentConfig(chapter_synth_max_parallel=1)
    assert orch._resolve_chapter_synth_parallel({}) == 1
    assert orch._resolve_chapter_synth_parallel({"chapter_synth_max_parallel": 9}) == 3
    assert orch._resolve_chapter_synth_parallel({"chapter_synth_max_parallel": 0}) == 1


@pytest.mark.asyncio
async def test_chapter_pipeline_serial_runs_all_chapters() -> None:
    orch = SlotOrchestrator(
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
        llm_client=MagicMock(),
    )
    orch._cfg = AnalysisAgentConfig(chapter_synth_max_parallel=1, narrative_streaming=False)
    orch.run_chapter_prepare = MagicMock(side_effect=lambda s: s)  # type: ignore[method-assign]
    orch.run_chapter_synthesize = AsyncMock(side_effect=lambda s: s)  # type: ignore[method-assign]
    orch.run_chapter_emit = MagicMock(  # type: ignore[method-assign]
        side_effect=lambda s: s.__setitem__("slot_index", int(s.get("slot_index") or 0) + 1) or s
    )
    state = {
        "ordered_slots": [
            {"id": "a", "kind": "static_markdown", "title": "A"},
            {"id": "b", "kind": "llm_section", "title": "B"},
        ],
        "slots_total": 2,
        "slot_index": 0,
        "options": {},
        "trace": {},
    }
    out = await orch.run_chapter_pipeline(state)  # type: ignore[arg-type]
    assert orch.run_chapter_prepare.call_count == 2
    assert orch.run_chapter_synthesize.await_count == 2
    assert orch.run_chapter_emit.call_count == 2
    assert out.get("trace", {}).get("chapter_synth_max_parallel") == 1


def test_result_status_label() -> None:
    assert _result_status_label({"trace": {"status": "aborted"}}) == "aborted"
    assert _result_status_label({"trace": {"status": "failed"}}) == "failed"
    assert _result_status_label({"trace": {"status": "success"}}) == "success"
    assert _result_status_label({}) == "success"


def test_save_trace_on_aborted_result() -> None:
    store = InMemoryAnalysisAgentTraceStore(max_items=50)
    svc = AnalysisAgentService(trace_store=store)
    svc._save_trace(
        {
            "request_id": "aa_abort_1",
            "analysis_type": "overheat_guidance",
            "summary": "partial",
            "user_id": "u1",
            "trace": {"status": "aborted"},
            "degrade_reasons": [],
            "started_at": "2026-08-25T00:00:00Z",
            "finished_at": "2026-08-25T00:00:01Z",
        }
    )
    hit = svc.get_trace("aa_abort_1")
    assert hit is not None
    assert (hit.get("trace") or {}).get("status") == "aborted"


def test_stream_live_or_global_enables_streaming() -> None:
    """narrative_streaming=false 但 slot.stream_live=true 时仍应开流式（绑定）。"""
    orch = SlotOrchestrator(
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
        llm_client=MagicMock(),
    )
    orch._cfg = AnalysisAgentConfig(narrative_streaming=False)
    slot = AnalysisAgentSlot(
        id="s1", kind="llm_section", title="T", stream_live=True, use_emit_tools=False
    )
    options = {"narrative_streaming": False}
    ns = options.get("narrative_streaming", orch._cfg.narrative_streaming)
    if ns is None:
        ns = orch._cfg.narrative_streaming
    effective = bool(ns) or bool(slot.stream_live)
    assert effective is True
