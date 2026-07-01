"""看图诊断视觉臂结果：供 HITL 中断 / SSE 前端展示（对齐报告「外观形貌智能分析 · 1.1」）。"""

from __future__ import annotations

from typing import Any

VISION_HITL_TITLE = "【图像可见分析】"

VISION_BOILER_REJECTION_DEFAULT = "当前图片非锅炉相关图片，请重新上传"


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
    for key in ("user_message", "preliminary_visual_conclusion", "notes"):
        val = vision_data.get(key) if isinstance(vision_data, dict) else None
        if isinstance(val, str) and val.strip():
            return val.strip()
    return VISION_BOILER_REJECTION_DEFAULT


def _as_list_text(val: Any, *, limit: int = 12) -> str | None:
    if val is None:
        return None
    if isinstance(val, list):
        items = [str(x).strip() for x in val if str(x).strip()]
        if not items:
            return None
        return "；".join(items[:limit])
    s = str(val).strip()
    return s or None


def _pick(data: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        v = data.get(k)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def _build_main_morphology(data: dict[str, Any], *, subtype: str) -> str | None:
    morph = _pick(data, "morphology_summary", "burst_morphology_summary")
    if morph:
        return str(morph).strip()
    parts: list[str] = []
    if subtype == "leakage_burst":
        main = _pick(data, "burst_type", "defect_type")
        if main:
            parts.append(str(main))
    else:
        main = _pick(data, "defect_type")
        orient = _pick(data, "defect_orientation")
        if main:
            parts.append(str(main))
        if orient:
            parts.append(f"走向{orient}" if "走向" not in str(orient) else str(orient))
    return "，".join(parts) if parts else None


def _extra_signal_bullets(data: dict[str, Any], *, subtype: str, limit: int = 2) -> list[str]:
    signals = _pick(data, "defect_signals", "burst_signals")
    if not isinstance(signals, list):
        return []
    out: list[str] = []
    for item in signals:
        text = str(item).strip()
        if not text:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    marking = _pick(data, "inspector_marking")
    if marking and str(marking).strip() not in ("无", "无明显", "none"):
        mark = str(marking).strip()
        if mark not in out and len(out) < limit:
            out.insert(0, f"检验标记：{mark}")
    return out[:limit]


def build_vision_morphology_bullets(
    vision_data: dict[str, Any] | None,
    *,
    img_diag_subtype: str,
) -> list[str]:
    """
    将视觉 JSON 映射为报告 1.1「缺陷宏观形貌特征」风格的 bullet 行（仅非空项）。
    固定顺序：主体形貌 → 分布特征 → 表面状态 → 其他可见要点。
    """
    if not isinstance(vision_data, dict) or vision_data.get("vision_skipped"):
        return []

    rejection = vision_boiler_rejection_message(vision_data)
    if rejection:
        return [rejection]

    subtype = (img_diag_subtype or "defect_ident").strip()
    data = vision_data
    bullets: list[str] = []

    main = _build_main_morphology(data, subtype=subtype)
    if main:
        bullets.append(f"主体形貌：{main}")

    dist = _pick(data, "distribution_features")
    if dist:
        bullets.append(f"分布特征：{dist}")

    surface = _pick(data, "surface_state")
    if surface:
        bullets.append(f"表面状态：{surface}")

    extras = _extra_signal_bullets(data, subtype=subtype, limit=2)
    if extras:
        bullets.append(f"其他可见要点：{'；'.join(extras)}")

    if not bullets and data.get("parse_error"):
        bullets.append("说明：视觉结果解析异常，请以后续台账确认与完整报告为准")
    return bullets


def build_vision_findings_display(
    vision_data: dict[str, Any] | None,
    *,
    img_diag_subtype: str,
) -> dict[str, Any]:
    """HITL / SSE 用简化展示 dict（与 build_vision_morphology_bullets 一致）。"""
    if not isinstance(vision_data, dict) or vision_data.get("vision_skipped"):
        reason = (vision_data or {}).get("reason") if isinstance(vision_data, dict) else None
        out: dict[str, Any] = {"说明": "未提供有效图片，暂无视觉分析结果"}
        if reason:
            out["跳过原因"] = str(reason)
        return out

    rejection = vision_boiler_rejection_message(vision_data)
    if rejection:
        return {"说明": rejection}

    bullets = build_vision_morphology_bullets(vision_data, img_diag_subtype=img_diag_subtype)
    display: dict[str, Any] = {}
    for line in bullets:
        if "：" in line:
            label, val = line.split("：", 1)
            display[label.strip()] = val.strip()
        else:
            display["说明"] = line
    return display


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
