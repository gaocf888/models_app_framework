"""T3：NL2SQL disable_qa_slot_replay 打穿 + 轻量质量门 L1。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.nl2sql_executor import plan_item_resolved, run_nl2sql_for_plan_item
from app.analysis_agent.quality import check_l1_anchors, required_anchors_for, resolve_quality_profile
from app.analysis_agent.report_spec import load_report_spec
from app.analysis_agent.slots.registry import clear_slot_cache, get_agent_slots
from app.analysis_agent.slots.specs import get_default_agent_template_version
from app.models.nl2sql import NL2SQLQueryRequest, NL2SQLQueryResponse


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


def test_required_anchors_by_type() -> None:
    assert required_anchors_for("overheat_guidance") == ("time",)
    assert required_anchors_for("subsidence_quarterly") == ("time", "zone")
    assert resolve_quality_profile(options={}, cfg_profile="light") == "light"
    assert resolve_quality_profile(options={"quality_profile": "strict_like"}, cfg_profile="light") == (
        "strict_like"
    )


def test_l1_missing_time_on_empty_query() -> None:
    out = check_l1_anchors(query="请做综合分析", analysis_type="overheat_guidance")
    assert "time" in out["missing"]
    assert "l1_missing_anchor:time" in out["degrade_reasons"]


def test_l1_subsidence_requires_zone() -> None:
    out = check_l1_anchors(
        query="请分析2024年第三季度沉降情况",
        analysis_type="subsidence_quarterly",
    )
    assert "time" not in out["missing"]
    assert "zone" in out["missing"]


def test_data_quality_records_l1_and_continues() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    state: AnalysisAgentState = {
        "query": "请做综合分析",
        "analysis_type": "overheat_guidance",
        "plan_tasks": [],
        "task_status": {},
        "options": {"strict": False, "quality_profile": "light"},
        "degrade_reasons": [],
    }
    out = orch.run_data_quality(state)
    assert out.get("abort_requested") is not True
    assert "l1_missing_anchor:time" in (out.get("degrade_reasons") or [])
    assert out.get("quality_l1", {}).get("missing") == ["time"]
    out2 = orch.run_data_quality(out)
    assert (out2.get("degrade_reasons") or []).count("l1_missing_anchor:time") == 1


def test_data_quality_l1_strict_like_aborts() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    state: AnalysisAgentState = {
        "query": "请做综合分析",
        "analysis_type": "overheat_guidance",
        "plan_tasks": [],
        "task_status": {},
        "options": {"strict": True, "quality_profile": "strict_like"},
        "degrade_reasons": [],
    }
    out = orch.run_data_quality(state)
    assert out.get("abort_requested") is True
    assert "l1" in str(out.get("error") or "")


@pytest.mark.asyncio
async def test_run_nl2sql_passes_disable_qa_slot_replay() -> None:
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(return_value=NL2SQLQueryResponse(sql="SELECT 1", rows=[{"v": 1}]))
    rows, rec = await run_nl2sql_for_plan_item(
        nl2sql=nl2sql,
        user_id="u1",
        session_id="s1",
        question="q1 question",
        item_id="q1",
        analysis_type="overheat_guidance",
        plan_template_version="analysis_agent_v1",
        analysis_request_id="aa_t3",
        query="用户原句昨天超温",
        disable_qa_slot_replay=True,
    )
    assert rows
    req = nl2sql.query.await_args.args[0]
    assert isinstance(req, NL2SQLQueryRequest)
    assert req.disable_qa_slot_replay is True
    assert req.plan_item_id == "q1"
    assert req.plan_template_version == "analysis_agent_v1"
    assert req.time_intent_text == "用户原句昨天超温"
    assert rec.get("disable_qa_slot_replay") is True


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
    assert req.disable_qa_slot_replay is True
    assert len(state["nl2sql_calls"]) == 2
    assert sum(1 for c in state["nl2sql_calls"] if c.get("cache_hit")) == 1
    assert state["gathered_data"]["q1"]
