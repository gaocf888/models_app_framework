from __future__ import annotations

"""综合分析智能体轻量质量门（L0 由 orchestrator 处理；本模块负责 L1 锚点）。"""

from typing import Any

from app.nl2sql.question_intent import resolve_question_intent

# 锅炉类分析：关键锚点为时间
_BOILER_TYPES = frozenset(
    {
        "overheat_guidance",
        "maintenance_strategy",
        "four_tube_health_interpretation",
        "leakage_burst_analysis",
    }
)


def required_anchors_for(analysis_type: str) -> tuple[str, ...]:
    """按 analysis_type 返回 L1 必需锚点名。"""
    at = (analysis_type or "").strip()
    if at.startswith("subsidence_"):
        return ("time", "zone")
    if at in _BOILER_TYPES:
        return ("time",)
    return ("time",)


def resolve_quality_profile(
    *,
    options: dict[str, Any] | None,
    cfg_profile: str,
) -> str:
    opts = options or {}
    raw = opts.get("quality_profile")
    if raw is None or str(raw).strip() == "":
        raw = cfg_profile
    profile = str(raw or "light").strip().lower()
    if profile not in ("light", "strict_like"):
        return "light"
    return profile


def check_l1_anchors(
    *,
    query: str,
    analysis_type: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    L1：解析用户原句关键锚点。

    地降 V2：options._resolved 已有 t_start/t_end 则视为 time 满足；
    area 空（全市）亦视为 zone 满足，避免空 query 误 degrade。
    """
    q = (query or "").strip()
    intent = resolve_question_intent(q, time_intent_source=q)
    has_time = bool(intent.time_window) or bool(intent.time_anchor)
    scope = intent.scope
    has_zone = bool(
        (scope.district or "").strip()
        or (scope.station_name or "").strip()
        or (scope.station_id or "").strip()
    )
    resolved = (options or {}).get("_resolved") if isinstance(options, dict) else None
    if isinstance(resolved, dict):
        if resolved.get("t_start") and resolved.get("t_end"):
            has_time = True
        # 结构化全市（area=None）或已指定区划，均视为 zone 已锚
        has_zone = True
    required = required_anchors_for(analysis_type)
    missing: list[str] = []
    if "time" in required and not has_time:
        missing.append("time")
    if "zone" in required and not has_zone:
        missing.append("zone")

    degrade_reasons = [f"l1_missing_anchor:{name}" for name in missing]
    return {
        "missing": missing,
        "degrade_reasons": degrade_reasons,
        "anchors": {
            "time_window_tag": intent.time_window_tag,
            "time_anchor_tag": intent.time_anchor_tag,
            "district": scope.district,
            "station_name": scope.station_name,
            "station_id": scope.station_id,
            "required": list(required),
        },
    }
