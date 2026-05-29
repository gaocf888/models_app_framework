"""各 analysis_type 的 plan 版本与章节合成 scene 映射。"""

from __future__ import annotations

# 综合分析智能体统一模板版本（多槽位流水线，不再区分 v1/v2）
DEFAULT_AGENT_TEMPLATE_VERSION = "v1"

PLAN_VERSION_BY_TYPE: dict[str, str] = {
    "overheat_guidance": DEFAULT_AGENT_TEMPLATE_VERSION,
    "maintenance_strategy": DEFAULT_AGENT_TEMPLATE_VERSION,
    "four_tube_health_interpretation": DEFAULT_AGENT_TEMPLATE_VERSION,
    "leakage_burst_analysis": DEFAULT_AGENT_TEMPLATE_VERSION,
}

NARRATIVE_SCENE_BY_TYPE: dict[str, str] = {
    "overheat_guidance": "analysis_agent_synthesis_overheat_guidance",
    "maintenance_strategy": "analysis_agent_synthesis_maintenance_strategy",
    "four_tube_health_interpretation": "analysis_agent_synthesis_four_tube_health_interpretation",
    "leakage_burst_analysis": "analysis_agent_synthesis_leakage_burst_analysis",
}

SUPPORTED_ANALYSIS_TYPES: tuple[str, ...] = tuple(PLAN_VERSION_BY_TYPE.keys())


def default_plan_version(analysis_type: str) -> str:
    return PLAN_VERSION_BY_TYPE.get(analysis_type, DEFAULT_AGENT_TEMPLATE_VERSION)


def default_slot_version(analysis_type: str) -> str:
    return default_plan_version(analysis_type)


def narrative_scene_for_type(analysis_type: str) -> str:
    return NARRATIVE_SCENE_BY_TYPE.get(
        analysis_type,
        f"analysis_agent_synthesis_{analysis_type}",
    )


def normalize_template_version(version: str | None) -> str:
    """将历史 v2 或空值规范为统一多槽位版本 v1。"""
    v = (version or "").strip().lower()
    if not v or v == "v2":
        return DEFAULT_AGENT_TEMPLATE_VERSION
    return v
