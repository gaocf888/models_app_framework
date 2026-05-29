from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis_agent.checkpoint import build_analysis_agent_checkpointer
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.runner import AnalysisAgentGraphRunner
from app.analysis_agent.session_store import create_resume_token, get_resume_session
from app.analysis_agent.slots.serialize import slot_to_dict
from app.core.config import get_app_config
from app.analysis_agent.slots.registry import get_agent_slots
from app.models.nl2sql import NL2SQLQueryResponse


def test_session_store_roundtrip() -> None:
    token = create_resume_token(
        thread_id="aa_test_thread",
        request_id="aa_test_req",
        user_id="u1",
        session_id="s1",
        analysis_type="overheat_guidance",
        interrupt_payload={"prompt": "confirm?"},
    )
    sess = get_resume_session(token)
    assert sess is not None
    assert sess.thread_id == "aa_test_thread"
    assert sess.user_id == "u1"


def test_build_checkpointer_memory() -> None:
    cp = build_analysis_agent_checkpointer()
    # 默认测试环境 ANALYSIS_AGENT_CHECKPOINT_BACKEND=memory
    assert cp is not None or get_app_config().analysis_agent.checkpoint_backend == "none"


def test_quality_gate_triggers_retry() -> None:
    orch = SlotOrchestrator(
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
    )
    slots = get_agent_slots("overheat_guidance")
    slot = next(s for s in slots if "q2a" in s.source_item_ids)
    idx = slots.index(slot)
    state = {
        "slot_index": idx,
        "ordered_slots": [slot_to_dict(s) for s in slots],
        "plan_tasks": [{"item_id": "q2a", "mandatory": True}],
        "task_status": {"q2a": "mandatory_empty"},
        "gathered_data": {},
        "options": {"enable_human_in_the_loop": False},
    }
    out = orch.run_slot_quality(state)
    assert out.get("slot_retry_nl2sql") is True


@pytest.mark.asyncio
async def test_resume_invalid_token() -> None:
    runner = AnalysisAgentGraphRunner(
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
    )
    events = []
    async for ev in runner.iter_resume_stream_events(
        resume_token="rt_invalid",
        user_id="u",
        session_id="s",
        action="skip_slot",
    ):
        events.append(ev)
    assert events[0]["event"] == "analysis_agent_error"


@pytest.mark.asyncio
async def test_interrupt_emits_user_input_required() -> None:
    hybrid = MagicMock()
    hybrid.retrieve = MagicMock(return_value=[])
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(return_value=NL2SQLQueryResponse(sql="SELECT 1", rows=[]))

    runner = AnalysisAgentGraphRunner(hybrid_rag=hybrid, nl2sql_service=nl2sql)
    if runner._graph is None:
        pytest.skip("langgraph not available")

    interrupt_payload = {
        "prompt": "关键数据缺失",
        "slot_id": "s03",
        "suggested_actions": ["retry", "skip_slot"],
    }

    async def fake_astream(_input, _config, stream_mode=None):
        yield {"initialize": {"pending_events": [{"event": "analysis_agent_meta", "request_id": "aa_x"}]}}
        yield {"__interrupt__": [type("I", (), {"value": interrupt_payload})()]}

    runner._graph.astream = fake_astream  # type: ignore[method-assign]
    runner._graph.aget_state = AsyncMock(return_value=MagicMock(interrupts=[]))  # type: ignore[method-assign]

    with patch("app.analysis_agent.graph.runner.create_resume_token", return_value="rt_test"):
        events = []
        async for ev in runner.iter_stream_events(
            user_id="u",
            session_id="s",
            analysis_type="overheat_guidance",
            query="test",
            options={"enable_rag": False},
            request_id="aa_interrupt_test",
        ):
            events.append(ev)
        assert any(e.get("event") == "analysis_agent_user_input_required" for e in events)
        hitl = [e for e in events if e.get("event") == "analysis_agent_user_input_required"][0]
        assert hitl.get("resume_token") == "rt_test"


@pytest.mark.asyncio
async def test_resume_stream_continues_after_command() -> None:
    hybrid = MagicMock()
    nl2sql = MagicMock()
    runner = AnalysisAgentGraphRunner(hybrid_rag=hybrid, nl2sql_service=nl2sql)
    if runner._graph is None:
        pytest.skip("langgraph not available")

    async def fake_astream(_input, _config, stream_mode=None):
        yield {
            "slot_emit": {
                "pending_events": [
                    {
                        "event": "analysis_agent_finished",
                        "request_id": "aa_resume",
                        "result": {
                            "request_id": "aa_resume",
                            "analysis_type": "overheat_guidance",
                            "summary": "done",
                            "structured_report": {},
                            "evidence": {},
                            "trace": {},
                        },
                    }
                ]
            }
        }

    runner._graph.astream = fake_astream  # type: ignore[method-assign]
    runner._graph.aget_state = AsyncMock(return_value=MagicMock(interrupts=[]))  # type: ignore[method-assign]

    with patch(
        "app.analysis_agent.graph.runner.get_resume_session",
        return_value=type(
            "S",
            (),
            {
                "resume_token": "rt_ok",
                "thread_id": "aa_resume",
                "request_id": "aa_resume",
                "user_id": "u",
                "session_id": "s",
                "analysis_type": "overheat_guidance",
                "created_at": 0.0,
                "interrupt_payload": {},
            },
        )(),
    ):
        with patch("app.analysis_agent.graph.runner.delete_resume_session"):
            events = []
            async for ev in runner.iter_resume_stream_events(
                resume_token="rt_ok",
                user_id="u",
                session_id="s",
                action="skip_slot",
            ):
                events.append(ev)
            assert any(e.get("event") == "analysis_agent_finished" for e in events)
