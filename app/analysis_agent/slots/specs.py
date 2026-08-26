"""各 analysis_type 的 plan 版本与章节合成 scene 映射。"""

from __future__ import annotations

REPORT_FALLBACK_SUFFIX = "analysis_agent"
DEFAULT_PLAN_TEMPLATE_VERSION_FALLBACK = "analysis_agent_v1"
_LEGACY_VERSION_ALIASES = frozenset({"v1", "v2"})

_SUBSIDENCE_TYPES = (
    "subsidence_daily",
    "subsidence_weekly",
    "subsidence_monthly",
    "subsidence_quarterly",
    "subsidence_yearly",
)

PLAN_VERSION_BY_TYPE: dict[str, str] = {
    "overheat_guidance": DEFAULT_PLAN_TEMPLATE_VERSION_FALLBACK,
    "maintenance_strategy": DEFAULT_PLAN_TEMPLATE_VERSION_FALLBACK,
    "four_tube_health_interpretation": DEFAULT_PLAN_TEMPLATE_VERSION_FALLBACK,
    "leakage_burst_analysis": DEFAULT_PLAN_TEMPLATE_VERSION_FALLBACK,
    **{t: DEFAULT_PLAN_TEMPLATE_VERSION_FALLBACK for t in _SUBSIDENCE_TYPES},
}

NARRATIVE_SCENE_BY_TYPE: dict[str, str] = {
    "overheat_guidance": "analysis_agent_synthesis_overheat_guidance",
    "maintenance_strategy": "analysis_agent_synthesis_maintenance_strategy",
    "four_tube_health_interpretation": "analysis_agent_synthesis_four_tube_health_interpretation",
    "leakage_burst_analysis": "analysis_agent_synthesis_leakage_burst_analysis",
    # 地降五类共用短 system；章节细节在 report JSON
    **{t: "analysis_agent_synthesis_subsidence" for t in _SUBSIDENCE_TYPES},
}

SUPPORTED_ANALYSIS_TYPES: tuple[str, ...] = tuple(PLAN_VERSION_BY_TYPE.keys())


def get_default_agent_template_version() -> str:
    """逻辑 plan 版本：来自 ANALYSIS_AGENT_PLAN_TEMPLATE_VERSION，供 NL2SQL QA 五元组隔离。"""
    try:
        from app.core.config import get_app_config

        v = (get_app_config().analysis_agent.plan_template_version or "").strip()
        if v:
            return v
    except Exception:  # noqa: BLE001
        pass
    return DEFAULT_PLAN_TEMPLATE_VERSION_FALLBACK


# 兼容旧 import；运行时请以 get_default_agent_template_version() 为准
DEFAULT_AGENT_TEMPLATE_VERSION = DEFAULT_PLAN_TEMPLATE_VERSION_FALLBACK


def default_plan_version(analysis_type: str) -> str:
    return PLAN_VERSION_BY_TYPE.get(analysis_type, get_default_agent_template_version())


def default_slot_version(analysis_type: str) -> str:
    return default_plan_version(analysis_type)


def narrative_scene_for_type(analysis_type: str) -> str:
    return NARRATIVE_SCENE_BY_TYPE.get(
        analysis_type,
        f"analysis_agent_synthesis_{analysis_type}",
    )


def normalize_template_version(version: str | None) -> str:
    """空值及历史 v1/v2 规范为 env 默认逻辑版本（与现网 /analysis 的 v1/v2 隔离）。"""
    v = (version or "").strip().lower()
    if not v or v in _LEGACY_VERSION_ALIASES:
        return get_default_agent_template_version()
    return v


def is_subsidence_type(analysis_type: str) -> bool:
    return (analysis_type or "").strip() in _SUBSIDENCE_TYPES
