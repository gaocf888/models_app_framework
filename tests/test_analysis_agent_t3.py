from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.nl2sql_executor import plan_item_resolved
from app.analysis_agent.report_spec import load_report_spec
from app.analysis_agent.slots.specs import get_default_agent_template_version
from app.analysis_agent.slots.registry import clear_slot_cache, get_agent_slots
from app.models.nl2sql import NL2SQLQueryResponse


@pytest.fixture(autouse=True)
def _clear_registry_cache() -> None:
    clear_slot_cache()
    yield
    clear_slot_cache()


def test_plan_item_resolved_covers_empty_and_status() -> None:
    assert not plan_item_resolved("q1", gathered_data={}, task_status={})
    assert plan_item_resolved("q1", gathered_data={"q1": []}, task_status={})
    assert plan_item_resolved("q1", gathered_data={}, task_status={"q1": "optional_empty"})


def test_overheat_report_spec_loads() -> None:
    spec = load_report_spec("overheat_guidance")
    assert spec is not None
    assert len(spec.chapters) == 9
    kinds = {s.kind for s in spec.chapters}
    assert "llm_section" in kinds
    assert "template_deterministic" not in kinds


def test_overheat_registry_uses_report_spec() -> None:
    slots = get_agent_slots("overheat_guidance")
    assert len(slots) == 9
    assert all(s.kind in ("llm_section", "static_markdown") for s in slots)


@pytest.mark.asyncio
async def test_acquire_slot_data_dedupes_plan_item_id() -> None:
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(return_value=NL2SQLQueryResponse(sql="SELECT 1", rows=[{"v": 1}]))
    hybrid = MagicMock()
    hybrid.retrieve = MagicMock(return_value=[])

    orch = SlotOrchestrator(nl2sql_service=nl2sql, hybrid_rag=hybrid)
    plan_tasks = [
        {
            "item_id": "q1",
            "question": "test q1",
            "mandatory": False,
        }
    ]
    slot_a = {
        "id": "s1",
        "kind": "llm_section",
        "title": "A",
        "source_item_ids": ["q1"],
        "narrative_instruction": "",
        "table_id": "s1",
        "template_id": "",
        "static_body": "",
        "table_kind": None,
        "chart_when_table": False,
        "mandatory_data": False,
        "max_nl2sql_retries": 0,
        "max_synthesize_retries": 0,
        "allow_human_confirm": False,
        "stream_live": False,
        "outline": (),
        "constraints": (),
        "allowed_outputs": (),
        "field_hints": (),
        "use_emit_tools": False,
    }
    slot_b = {**slot_a, "id": "s2", "title": "B", "table_id": "s2"}
    from app.analysis_agent.slots.serialize import slot_from_dict

    default_ver = get_default_agent_template_version()
    state: AnalysisAgentState = {
        "user_id": "u1",
        "session_id": "s1",
        "request_id": "aa_test",
        "analysis_type": "maintenance_strategy",
        "query": "分析",
        "options": {"plan_template_version": default_ver, "max_rows_per_query": 100},
        "trace": {"plan_template_version": default_ver},
        "plan_tasks": plan_tasks,
        "ordered_slots": [slot_a, slot_b],
        "slot_index": 0,
        "gathered_data": {},
        "task_status": {},
        "nl2sql_calls": [],
    }

    await orch._acquire_slot_data(state, slot_from_dict(slot_a), plan_tasks)
    await orch._acquire_slot_data(state, slot_from_dict(slot_b), plan_tasks)

    assert nl2sql.query.await_count == 1
    req = nl2sql.query.await_args_list[0].args[0]
    assert req.analysis_type == "maintenance_strategy"
    assert req.plan_item_id == "q1"
    assert req.plan_template_version == default_ver
    assert len(state["nl2sql_calls"]) == 2
    assert sum(1 for c in state["nl2sql_calls"] if c.get("cache_hit")) == 1
    assert state["gathered_data"]["q1"]
