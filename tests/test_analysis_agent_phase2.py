from __future__ import annotations

import pytest

from app.analysis_agent.plans.loader import (
    effective_plan_version,
    get_synthesis_template,
    load_plan_tasks,
    synthesis_scene_candidates,
)
from app.analysis_agent.slots.registry import (
    SUPPORTED_ANALYSIS_TYPES,
    get_agent_slots,
    registry_available,
)
from app.analysis_agent.slots.specs import (
    default_plan_version,
    get_default_agent_template_version,
    narrative_scene_for_type,
)


@pytest.mark.parametrize(
    "analysis_type,min_slots",
    [
        ("overheat_guidance", 8),
        ("maintenance_strategy", 2),
        ("four_tube_health_interpretation", 3),
        ("leakage_burst_analysis", 4),
    ],
)
def test_registry_all_analysis_types(analysis_type: str, min_slots: int) -> None:
    assert analysis_type in SUPPORTED_ANALYSIS_TYPES
    assert registry_available(analysis_type)
    slots = get_agent_slots(analysis_type)
    assert len(slots) >= min_slots


@pytest.mark.parametrize(
    "analysis_type,version,expected_item",
    [
        ("overheat_guidance", None, "q1"),
        ("maintenance_strategy", None, "q0"),
        ("four_tube_health_interpretation", None, "q1"),
        ("leakage_burst_analysis", None, "q1"),
    ],
)
def test_plan_loads_with_fallback(
    analysis_type: str, version: str, expected_item: str
) -> None:
    tasks = load_plan_tasks(analysis_type, version=version)
    ids = {t["item_id"] for t in tasks}
    assert expected_item in ids


def test_default_plan_version_by_type() -> None:
    default_ver = get_default_agent_template_version()
    assert default_plan_version("overheat_guidance") == default_ver
    assert default_plan_version("maintenance_strategy") == default_ver
    assert effective_plan_version("maintenance_strategy", {}) == default_ver
    assert effective_plan_version("overheat_guidance", {}) == default_ver
    assert effective_plan_version("overheat_guidance", {"plan_template_version": "v2"}) == default_ver


def test_synthesis_template_resolves() -> None:
    for analysis_type in SUPPORTED_ANALYSIS_TYPES:
        tpl, scene = get_synthesis_template(analysis_type)
        assert tpl is not None
        assert (tpl.content or "").strip()
        assert scene in synthesis_scene_candidates(analysis_type)
    assert narrative_scene_for_type("maintenance_strategy").startswith("analysis_agent_")


def test_metrics_importable() -> None:
    from app.core.metrics import (  # noqa: F401
        ANALYSIS_AGENT_DEGRADE_COUNT,
        ANALYSIS_AGENT_NL2SQL_CALL_COUNT,
        ANALYSIS_AGENT_REQUEST_COUNT,
        ANALYSIS_AGENT_SLOT_LATENCY,
    )

    assert ANALYSIS_AGENT_REQUEST_COUNT is not None
