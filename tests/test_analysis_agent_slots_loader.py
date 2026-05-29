from __future__ import annotations

import json

import pytest

from app.analysis_agent.slots.builder import slot_from_dict, slots_from_spec_dict
from app.analysis_agent.slots.loader import load_agent_slots
from app.analysis_agent.slots.registry import clear_slot_cache, get_agent_slots
from app.analysis_agent.tools.agent_tools import _tool_emit_markdown_table
from app.analysis_agent.tools.slot_context import reset_slot_tool_context, set_slot_tool_context


def test_slot_from_dict_llm_section() -> None:
    slot = slot_from_dict(
        {
            "id": "t1",
            "kind": "llm_section",
            "source_item_ids": ["q1"],
            "outline": ["要点1"],
            "allowed_outputs": ["paragraph", "table"],
        }
    )
    assert slot.kind == "llm_section"
    assert slot.use_emit_tools is True
    assert slot.outline == ("要点1",)


def test_load_maintenance_slots_from_json() -> None:
    clear_slot_cache()
    slots = load_agent_slots("maintenance_strategy", version="v1")
    assert len(slots) >= 2
    kinds = {s.kind for s in slots}
    assert "llm_section" in kinds
    assert slots[0].kind == "static_markdown"


def test_registry_uses_config_slots() -> None:
    clear_slot_cache()
    slots = get_agent_slots("four_tube_health_interpretation")
    assert any(s.kind == "llm_section" for s in slots)


def test_emit_markdown_table_tool() -> None:
    token = set_slot_tool_context(
        {
            "slot_id": "test",
            "source_item_ids": ("q1",),
            "section_artifacts": {"tables": [], "charts": [], "table_markdowns": []},
        }
    )
    try:
        payload = json.dumps(
            {
                "title": "测试表",
                "columns": ["区域", "次数"],
                "rows": [{"区域": "过热器", "次数": 3}],
                "table_kind": "classification",
            },
            ensure_ascii=False,
        )
        msg = _tool_emit_markdown_table(payload)
        assert "已登记表格" in msg
    finally:
        reset_slot_tool_context(token)


def test_slots_plan_item_validation() -> None:
    from app.analysis_agent.context_loader import load_analysis_run_context

    slots = [
        slot_from_dict(
            {
                "id": "x",
                "kind": "llm_section",
                "source_item_ids": ["q_not_exist"],
            }
        )
    ]
    ctx = load_analysis_run_context("maintenance_strategy", version="v1")
    with pytest.raises(ValueError, match="report_plan_mismatch"):
        from app.analysis_agent.context_loader import _validate_slots_plan_refs

        _validate_slots_plan_refs("maintenance_strategy", "v1", slots, ctx.plan_tasks)
