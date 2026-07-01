"""看图诊断视觉臂结果：供 HITL 中断 / SSE 前端展示（对齐报告「外观形貌智能分析 · 1.1」）。"""

from __future__ import annotations

import re
from typing import Any

VISION_HITL_TITLE = "【图像可见分析】"
VISION_FRONTEND_NARRATIVE_LABEL = "外观可见分析"

VISION_BOILER_REJECTION_DEFAULT = "当前图片非锅炉相关图片，请重新上传"

_VISION_NARRATIVE_CUT_MARKERS = ("---JSON---",)

# 前端展示不输出的内部/结构化字段（完整 vision_findings 仍保留在状态机）
_VISION_DISPLAY_SKIP_KEYS = frozenset({
    "parse_error",
    "raw_text",
    "vision_skipped",
    "reason",
    "vision_lane_error",
    "is_boiler_pressure_part_image",
    "vision_narrative",
})


def is_vision_boiler_relevance_rejected(vision_data: dict[str, Any] | None) -> bool:
    """视觉 JSON 标明非锅炉受压部件/管壁相关图（仅用于展示，不阻断链路）。"""
    if not isinstance(vision_data, dict) or vision_data.get("vision_skipped"):
        return False
    flag = vision_data.get("is_boiler_pressure_part_image")
    if flag is False:
        return True
    if flag is True:
        return False
    return False


def vision_boiler_rejection_message(vision_data: dict[str, Any] | None) -> str | None:
    if not is_vision_boiler_relevance_rejected(vision_data):
        return None
    for key in ("user_message", "preliminary_visual_conclusion", "notes", "vision_narrative"):
        val = vision_data.get(key) if isinstance(vision_data, dict) else None
        if isinstance(val, str) and val.strip():
            return sanitize_vision_narrative_for_frontend(val) or val.strip()
    return VISION_BOILER_REJECTION_DEFAULT


def sanitize_vision_narrative_for_frontend(raw: str) -> str:
    """
    去除 Markdown 格式标识、JSON 分隔段等，仅供接口返回前端展示。
    不修改状态机内原始 ``vision_narrative``。
    """
    text = (raw or "").strip()
    if not text:
        return ""

    for marker in _VISION_NARRATIVE_CUT_MARKERS:
        idx = text.upper().find(marker.upper())
        if idx != -1:
            text = text[:idx].strip()

    hr = re.search(r"\n\s*---\s*\n", text)
    if hr:
        text = text[: hr.start()].strip()

    lines_out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s in {"---", "---JSON---"}:
            continue

        s = re.sub(r"^#+\s*", "", s)
        s = re.sub(r"^Markdown\s+", "", s, flags=re.IGNORECASE)
        s = re.sub(r"^外观可见分析[：:]\s*", "", s)
        s = re.sub(r"^Markdown\s*外观可见分析\s*$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"^外观可见分析\s*$", "", s)

        if re.fullmatch(r"`+", s):
            continue

        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
        s = re.sub(r"`([^`]+)`", r"\1", s)

        if s.startswith("- "):
            s = s[2:].strip()
        elif s.startswith("* "):
            s = s[2:].strip()

        if s:
            lines_out.append(s)

    return "\n".join(lines_out)


def extract_frontend_vision_narrative(vision_data: dict[str, Any] | None) -> str:
    """从 vision_findings 提取供前端展示的外观可见分析叙述（已清理 Markdown 标识）。"""
    if not isinstance(vision_data, dict) or vision_data.get("vision_skipped"):
        return ""

    rejection = vision_boiler_rejection_message(vision_data)
    if rejection:
        return rejection

    raw_narrative = vision_data.get("vision_narrative")
    if isinstance(raw_narrative, str) and raw_narrative.strip():
        return sanitize_vision_narrative_for_frontend(raw_narrative)

    return ""


def build_vision_morphology_bullets(
    vision_data: dict[str, Any] | None,
    *,
    img_diag_subtype: str,
) -> list[str]:
    """HITL / SSE bullet 行：仅外观可见分析叙述（不含 JSON 结构化字段摘要）。"""
    del img_diag_subtype  # 前端叙述展示与子类型无关
    if not isinstance(vision_data, dict) or vision_data.get("vision_skipped"):
        return []

    narrative = extract_frontend_vision_narrative(vision_data)
    if narrative:
        return [ln for ln in narrative.splitlines() if ln.strip()]

    if vision_data.get("parse_error"):
        return ["说明：视觉结果解析异常，请以后续台账确认与完整报告为准"]
    return []


def build_vision_findings_display(
    vision_data: dict[str, Any] | None,
    *,
    img_diag_subtype: str,
) -> dict[str, Any]:
    """HITL / SSE 用展示 dict：仅 ``外观可见分析`` 叙述，不含结构化 JSON 字段。"""
    del img_diag_subtype
    if not isinstance(vision_data, dict) or vision_data.get("vision_skipped"):
        reason = (vision_data or {}).get("reason") if isinstance(vision_data, dict) else None
        out: dict[str, Any] = {"说明": "未提供有效图片，暂无视觉分析结果"}
        if reason:
            out["跳过原因"] = str(reason)
        return out

    rejection = vision_boiler_rejection_message(vision_data)
    if rejection:
        return {"说明": rejection}

    narrative = extract_frontend_vision_narrative(vision_data)
    if narrative:
        return {VISION_FRONTEND_NARRATIVE_LABEL: narrative}

    if vision_data.get("parse_error"):
        return {"说明": "视觉结果解析异常，请以后续台账确认与完整报告为准"}
    return {"说明": "（暂无可见形貌描述）"}


def format_vision_findings_display_lines(
    vision_data: dict[str, Any] | None,
    *,
    img_diag_subtype: str,
) -> list[str]:
    return build_vision_morphology_bullets(vision_data, img_diag_subtype=img_diag_subtype)


def format_vision_hitl_assistant_block(
    vision_data: dict[str, Any] | None,
    *,
    img_diag_subtype: str,
) -> str:
    bullets = build_vision_morphology_bullets(vision_data, img_diag_subtype=img_diag_subtype)
    if not bullets:
        return ""
    body = "\n".join(f"  · {ln}" for ln in bullets)
    return f"{VISION_HITL_TITLE}\n{body}"
