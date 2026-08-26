"""T2：stop + 叙述真流式 + ReAct 限用。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis_agent.agents.section_agent import (
    _should_use_react,
    section_result_to_slot_output,
    synthesize_section,
)
from app.analysis_agent.agents.section_result import SectionSynthesisResult
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.runner import AnalysisAgentGraphRunner
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.services.analysis_agent_stream_control import AnalysisAgentStreamControl


def test_should_use_react_only_emit_tools() -> None:
    plain = AnalysisAgentSlot(
        id="s1",
        kind="llm_section",
        title="叙述",
        use_emit_tools=False,
    )
    emit = AnalysisAgentSlot(
        id="s2",
        kind="llm_section",
        title="工具章",
        use_emit_tools=True,
    )
    assert _should_use_react(use_react_agent=True, slot=plain) is False
    assert _should_use_react(use_react_agent=True, slot=emit) is True
    assert _should_use_react(use_react_agent=False, slot=emit) is False


def test_section_result_already_streamed_skips_chunks() -> None:
    slot = AnalysisAgentSlot(id="s1", kind="llm_section", title="章", use_emit_tools=False)
    result = SectionSynthesisResult(markdown="正文ABC")
    out, chunks = section_result_to_slot_output(slot, result, already_streamed=True)
    assert "正文ABC" in out.markdown
    assert chunks == []
    _, chunks2 = section_result_to_slot_output(slot, result, already_streamed=False)
    assert chunks2


@pytest.mark.asyncio
async def test_stream_control_cancel() -> None:
    ctrl = AnalysisAgentStreamControl()
    sid = ctrl.begin_stream("u1", "s1")
    assert await ctrl.is_cancelled("u1", "s1", sid) is False
    await ctrl.cancel_stream("u1", "s1", sid)
    assert await ctrl.is_cancelled("u1", "s1", sid) is True
    await ctrl.clear_stream("u1", "s1", sid)
    assert await ctrl.is_cancelled("u1", "s1", sid) is False


@pytest.mark.asyncio
async def test_runner_cancel_checker() -> None:
    ctrl = AnalysisAgentStreamControl()
    runner = AnalysisAgentGraphRunner(
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
        stream_control=ctrl,
    )
    sid = ctrl.begin_stream("u1", "s1")
    check = runner._build_stream_cancel_checker("u1", "s1", sid)
    assert check is not None
    assert await check() is False
    await ctrl.cancel_stream("u1", "s1", sid)
    assert await check() is True


@pytest.mark.asyncio
async def test_synthesize_section_stream_chat_deltas() -> None:
    async def _gen(**_kwargs):
        for piece in ("甲", "乙", "丙"):
            yield piece

    client = MagicMock()
    client.stream_chat = _gen
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    slot = AnalysisAgentSlot(id="s1", kind="llm_section", title="T", use_emit_tools=False)
    prompts = MagicMock()
    prompts.get = MagicMock(return_value=None)

    with patch(
        "app.analysis_agent.agents.section_agent.get_synthesis_template",
        return_value=(None, None),
    ):
        result = await synthesize_section(
            prompts=prompts,
            slot=slot,
            query="q",
            gathered_data={},
            context_snippets=[],
            task_status={},
            hybrid_rag=None,
            analysis_type="overheat_guidance",
            llm_client=client,
            use_react_agent=False,
            on_delta=on_delta,
        )
    assert result.markdown == "甲乙丙"
    assert deltas == ["甲", "乙", "丙"]


@pytest.mark.asyncio
async def test_synthesize_slot_live_pushes_pending_without_fake_chunks() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())

    async def fake_synth(**kwargs):
        on_delta = kwargs.get("on_delta")
        if on_delta:
            await on_delta("live-token")
        return SectionSynthesisResult(markdown="live-token")

    slot = AnalysisAgentSlot(
        id="narr1",
        kind="llm_section",
        title="概述",
        use_emit_tools=False,
        source_item_ids=[],
    )
    state: AnalysisAgentState = {
        "request_id": "aa_t2",
        "query": "q",
        "analysis_type": "overheat_guidance",
        "options": {"narrative_streaming": True, "use_react_agent": False},
        "gathered_data": {},
        "task_status": {},
        "intent_context": [],
        "pending_events": [],
        "slot_index": 0,
        "summary_parts": [],
        "structured_report": {"sections": [], "tables": [], "charts": []},
        "slot_trace": [],
    }

    with patch(
        "app.analysis_agent.graph.orchestrator.synthesize_section",
        side_effect=fake_synth,
    ):
        out, chunks, live = await orch._synthesize_slot(state, slot)

    assert live is True
    assert chunks == []
    events = state.get("pending_events") or []
    types = [e.get("event") for e in events]
    assert "analysis_agent_chapter_start" in types
    assert "analysis_agent_summary_delta" in types
    assert any(
        e.get("text") == "live-token"
        for e in events
        if e.get("event") == "analysis_agent_summary_delta"
    )
    assert any("概述" in str(e.get("text") or "") for e in events)

    state["_narrative_live_streamed"] = True
    emit_evs = orch._emit_slot_output(state, slot, out, chunks, 0)
    assert not any(e.get("event") == "analysis_agent_summary_delta" for e in emit_evs)
    assert not any(e.get("event") == "analysis_agent_chapter_start" for e in emit_evs)
    assert any(e.get("event") == "analysis_agent_chapter_complete" for e in emit_evs)


@pytest.mark.asyncio
async def test_acquire_stops_on_cancel() -> None:
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(side_effect=AssertionError("should not run after cancel"))
    orch = SlotOrchestrator(nl2sql_service=nl2sql, hybrid_rag=MagicMock())

    async def cancelled() -> bool:
        return True

    state: AnalysisAgentState = {
        "user_id": "u1",
        "session_id": "s1",
        "request_id": "aa_cancel",
        "analysis_type": "overheat_guidance",
        "query": "q",
        "options": {},
        "plan_tasks": [{"item_id": "q1", "question": "x", "mandatory": True}],
        "gathered_data": {},
        "task_status": {},
        "nl2sql_calls": [],
        "_cancel_checker": cancelled,
        "pending_events": [],
    }
    await orch.run_acquire_data(state)
    assert state.get("abort_requested") is True
    assert state.get("error") == "user_cancelled"
    events = state.get("pending_events") or []
    assert any(e.get("event") == "analysis_agent_cancelled" for e in events)


@pytest.mark.asyncio
async def test_data_quality_preserves_user_cancelled() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    state: AnalysisAgentState = {
        "abort_requested": True,
        "error": "user_cancelled",
        "plan_tasks": [],
        "task_status": {},
        "options": {},
    }
    out = orch.run_data_quality(state)
    assert out.get("abort_requested") is True
    assert out.get("error") == "user_cancelled"
