"""看图诊断视觉臂结果：供 HITL 中断 / SSE 前端展示（对齐报告「外观形貌智能分析 · 1.1」）。"""

from __future__ import annotations

import re
from typing import Any

VISION_HITL_TITLE = "【图像可见分析】"
VISION_FRONTEND_NARRATIVE_LABEL = "外观可见分析"

VISION_BOILER_REJECTION_DEFAULT = "当前图片非锅炉相关图片，请重新上传"
VISION_HITL_REUPLOAD_PROMPT = "当前图片非锅炉相关图片，请重新上传后再确认台账。"
VISION_REJECT_INTERRUPT_REASON = "vision_boiler_image_rejected"

_VISION_NARRATIVE_CUT_MARKERS = ("---JSON---",)

# 仅当 JSON 标 false 但叙述/字段明确为锅炉管壁缺陷时，才视为「模型自相矛盾」而放行
_BOILER_VISION_DOMAIN_KEYWORDS = (
    "管壁",
    "管轴",
    "管排",
    "受热面",
    "锅炉",
    "焊缝",
    "吹灰孔",
    "水冷壁",
    "过热器",
    "再热器",
    "省煤器",
    "鳍片",
    "弯管",
    "承压管",
)

_BOILER_DEFECT_KEYWORDS = (
    "裂纹",
    "沟槽",
    "胀粗",
    "蠕变",
    "腐蚀",
    "剥落",
    "爆口",
    "泄漏",
    "开裂",
)

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
    if flag is True:
        return False
    if flag is False:
        if _vision_has_substantive_boiler_defect_analysis(vision_data):
            return False
        return True
    return False


def _text_has_boiler_domain(text: str) -> bool:
    return any(k in text for k in _BOILER_VISION_DOMAIN_KEYWORDS)


def _text_has_boiler_defect_signal(text: str) -> bool:
    return any(k in text for k in _BOILER_DEFECT_KEYWORDS)


def _vision_has_substantive_boiler_defect_analysis(vision_data: dict[str, Any]) -> bool:
    """
    JSON 标 false 但形貌明确为锅炉管壁缺陷时，视为相关图。
    仅修正 JSON 与 Markdown 矛盾；非锅炉设备的长描述不得放行。
    """
    parts: list[str] = []
    for key in (
        "defect_type",
        "morphology_summary",
        "preliminary_visual_conclusion",
        "vision_narrative",
    ):
        val = vision_data.get(key)
        if isinstance(val, str) and val.strip():
            parts.append(val)
    defect_types = vision_data.get("defect_types")
    if isinstance(defect_types, list):
        parts.extend(str(x) for x in defect_types if str(x).strip())
    signals = vision_data.get("defect_signals")
    if isinstance(signals, list):
        parts.extend(str(x) for x in signals if str(x).strip())

    combined = " ".join(parts)
    if not combined.strip():
        return False
    if not _text_has_boiler_domain(combined):
        return False

    defect_type = vision_data.get("defect_type")
    if isinstance(defect_type, str) and defect_type.strip() and "非锅炉" not in defect_type:
        return True
    if isinstance(defect_types, list) and any(str(x).strip() for x in defect_types):
        return True
    return _text_has_boiler_defect_signal(combined)


def vision_boiler_rejection_message(vision_data: dict[str, Any] | None) -> str | None:
    if not is_vision_boiler_relevance_rejected(vision_data):
        return None
    if not isinstance(vision_data, dict):
        return VISION_BOILER_REJECTION_DEFAULT

    user_msg = vision_data.get("user_message")
    if isinstance(user_msg, str) and user_msg.strip() and "重新上传" in user_msg:
        cleaned = sanitize_vision_narrative_for_frontend(user_msg)
        return cleaned or user_msg.strip()

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


def img_diag_request_has_images(
    img_diag_request: dict[str, Any] | None,
    *,
    img_diag_subtype: str,
) -> bool:
    """缺陷识别有图即校验；泄爆无图时不做锅炉图门禁。"""
    from app.llm.graphs.img_diag_hitl_images import normalize_image_url_list

    urls = normalize_image_url_list(
        (img_diag_request or {}).get("image_urls") if isinstance(img_diag_request, dict) else None
    )
    if not urls:
        return False
    subtype = (img_diag_subtype or "defect_ident").strip()
    if subtype == "leakage_burst":
        return bool(urls)
    return bool(urls)


def is_scope_confirm_blocked_by_vision(
    vision_data: dict[str, Any] | None,
    *,
    img_diag_request: dict[str, Any] | None,
    img_diag_subtype: str,
) -> bool:
    """台账可确认前：有图且视觉拒识则阻断。"""
    if not img_diag_request_has_images(img_diag_request, img_diag_subtype=img_diag_subtype):
        return False
    return is_vision_boiler_relevance_rejected(vision_data)


def apply_vision_rejection_scope_gate(state: dict[str, Any]) -> bool:
    """
    图片非锅炉相关：阻断 scope 确认完成，但台账区仍只展示 scope 文案。
    视觉换图提示由「图像可见分析」区单独展示。
    返回 True 表示须再次人机协同（换图或确认）。
    """
    req = state.get("img_diag_request") if isinstance(state.get("img_diag_request"), dict) else {}
    subtype = str(state.get("img_diag_subtype") or req.get("img_diag_subtype") or "defect_ident")
    vision_data = state.get("vision_prefetch_data")
    if not is_scope_confirm_blocked_by_vision(
        vision_data if isinstance(vision_data, dict) else None,
        img_diag_request=req,
        img_diag_subtype=subtype,
    ):
        state.pop("vision_confirm_blocked", None)
        if str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON:
            state.pop("interrupt_reason", None)
            scope_reason = str(state.get("scope_interrupt_reason") or "").strip()
            if scope_reason:
                state["interrupt_reason"] = scope_reason
            elif state.get("pending_matched_confirm"):
                state["interrupt_reason"] = "db_validate_matched"
        return False

    prior_reason = str(state.get("interrupt_reason") or "").strip()
    prior_prompt = str(state.get("human_prompt") or "").strip()
    if prior_reason and prior_reason != VISION_REJECT_INTERRUPT_REASON:
        state["scope_interrupt_reason"] = prior_reason
    if prior_prompt and prior_prompt != VISION_HITL_REUPLOAD_PROMPT:
        state["scope_hitl_prompt"] = prior_prompt

    if state.get("pending_matched_confirm"):
        from app.llm.graphs.img_diag_scope_display import SCOPE_HITL_DB_MATCHED_PROMPT

        state.setdefault("scope_interrupt_reason", "db_validate_matched")
        state.setdefault("scope_hitl_prompt", SCOPE_HITL_DB_MATCHED_PROMPT)

    state.pop("confirmed_scope_intent", None)
    state.pop("scope_intent_text", None)
    state["vision_confirm_blocked"] = True
    state["interrupt_reason"] = VISION_REJECT_INTERRUPT_REASON
    state["needs_db_retry"] = False
    state["validation_error"] = None

    from app.llm.graphs.img_diag_scope_display import resolve_scope_hitl_display_prompt

    state["human_prompt"] = resolve_scope_hitl_display_prompt(state=state)
    return True
