from __future__ import annotations

from functools import lru_cache

from app.analysis_agent.context_loader import load_analysis_run_context
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.slots.specs import (
    DEFAULT_AGENT_TEMPLATE_VERSION,
    SUPPORTED_ANALYSIS_TYPES,
    default_plan_version,
    narrative_scene_for_type,
    normalize_template_version,
)


@lru_cache(maxsize=16)
def _cached_context(analysis_type: str, version: str) -> tuple[AnalysisAgentSlot, ...]:
    ctx = load_analysis_run_context(analysis_type, version=version, validate_plan_refs=True)
    return tuple(ctx.slots)


def registry_available(analysis_type: str) -> bool:
    if analysis_type not in SUPPORTED_ANALYSIS_TYPES:
        return False
    try:
        return bool(_cached_context(analysis_type, default_plan_version(analysis_type)))
    except ValueError:
        return False


def get_agent_slots(
    analysis_type: str,
    *,
    slot_template_version: str | None = None,
) -> list[AnalysisAgentSlot]:
    ver = slot_template_version or default_plan_version(analysis_type)
    return list(_cached_context(analysis_type, ver))


def clear_slot_cache() -> None:
    _cached_context.cache_clear()


__all__ = [
    "DEFAULT_AGENT_TEMPLATE_VERSION",
    "SUPPORTED_ANALYSIS_TYPES",
    "default_plan_version",
    "narrative_scene_for_type",
    "normalize_template_version",
    "registry_available",
    "get_agent_slots",
    "clear_slot_cache",
]
