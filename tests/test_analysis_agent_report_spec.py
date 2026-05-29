from __future__ import annotations

import pytest

from app.analysis_agent.context_loader import load_analysis_run_context
from app.analysis_agent.report_spec import load_report_spec, report_spec_available
from app.analysis_agent.slots.registry import clear_slot_cache, get_agent_slots
from app.analysis_agent.slots.specs import get_default_agent_template_version


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_slot_cache()
    yield
    clear_slot_cache()


@pytest.mark.parametrize(
    "analysis_type",
    [
        "overheat_guidance",
        "maintenance_strategy",
        "four_tube_health_interpretation",
        "leakage_burst_analysis",
    ],
)
def test_report_spec_available(analysis_type: str) -> None:
    assert report_spec_available(analysis_type)


def test_load_analysis_run_context_overheat() -> None:
    ctx = load_analysis_run_context("overheat_guidance")
    assert ctx.from_report_spec
    assert len(ctx.slots) == 9
    assert all(s.kind in ("llm_section", "static_markdown") for s in ctx.slots)
    ids = {t["item_id"] for t in ctx.plan_tasks}
    assert "q1" in ids and "q6d" in ids


def test_registry_no_legacy_overheat_slots() -> None:
    slots = get_agent_slots("overheat_guidance")
    assert len(slots) == 9
    assert "template_deterministic" not in {s.kind for s in slots}


def test_load_report_spec_maintenance() -> None:
    spec = load_report_spec("maintenance_strategy")
    assert spec is not None
    assert len(spec.chapters) >= 2
