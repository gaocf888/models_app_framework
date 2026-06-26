"""看图诊断 scope 人机协同：台账字段中文展示映射。"""

from __future__ import annotations

from typing import Any

SCOPE_FIELD_LABELS: dict[str, str] = {
    "boiler": "机组",
    "device_name": "受热面",
    "check_location_name": "检测位置",
    "row_no": "排数",
    "tube_no": "管数",
    # 兼容旧字段
    "piperow_name": "检测位置",
}

SCOPE_FIELD_LABELS_CN_TO_EN: dict[str, str] = {
    cn: en for en, cn in SCOPE_FIELD_LABELS.items() if en != "piperow_name"
}
SCOPE_FIELD_LABELS_CN_TO_EN["管排名称"] = "check_location_name"

SCOPE_HITL_DISPLAY_FIELDS: tuple[str, ...] = (
    "boiler",
    "device_name",
    "check_location_name",
    "row_no",
    "tube_no",
)

SCOPE_HITL_TITLE = "【台账信息确认】"

SCOPE_HITL_DB_NOT_MATCHED_PROMPT = (
    "业务库中未匹配到下面台账信息，请确认机组、受热面、检测位置、排数、管数是否准确"
)

SCOPE_HITL_RELAXED_PROMPT = (
    "业务库未匹配到最细粒度范围，系统已自动放宽范围条件后继续查询；"
    "请确认下列台账信息，或修改后重新提交"
)


def scope_field_label(field: str) -> str:
    return SCOPE_FIELD_LABELS.get(field, field)


def format_missing_fields_cn(fields: list[str]) -> str:
    return "、".join(scope_field_label(f) for f in fields if f)


def scope_draft_to_display(scope_draft: dict[str, Any] | None) -> dict[str, Any]:
    """将 scope_draft 英文字段名映射为中文键（HITL 展示全部范围字段）。"""
    if not scope_draft:
        return {}
    from app.llm.graphs.img_diag_scope_intent import normalize_img_diag_scope_dict

    normalized = normalize_img_diag_scope_dict(scope_draft)
    display: dict[str, Any] = {}
    for en in SCOPE_HITL_DISPLAY_FIELDS:
        cn = SCOPE_FIELD_LABELS[en]
        val = normalized.get(en)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        display[cn] = val
    return display


def format_scope_draft_display_lines(scope_draft: dict[str, Any] | None) -> list[str]:
    return [f"{cn}：{val}" for cn, val in scope_draft_to_display(scope_draft).items()]


def format_scope_hitl_assistant_message(interrupt_payload: dict[str, Any] | None) -> str:
    """将 scope HITL interrupt 载荷格式化为可写入会话历史的 assistant 正文。"""
    if not interrupt_payload:
        return SCOPE_HITL_TITLE
    lines: list[str] = [SCOPE_HITL_TITLE]
    prompt = str(interrupt_payload.get("prompt") or "").strip()
    if prompt:
        lines.append(prompt)
    missing = interrupt_payload.get("missing_fields") or []
    if missing:
        lines.append(f"待补充：{'、'.join(str(x) for x in missing if str(x).strip())}")
    val_err = interrupt_payload.get("validation_error")
    if val_err:
        lines.append(f"校验说明：{val_err}")
    display = interrupt_payload.get("scope_draft_display")
    if isinstance(display, dict) and display:
        lines.append("当前解析：")
        for key, val in display.items():
            if val is None or (isinstance(val, str) and not val.strip()):
                continue
            lines.append(f"  · {key}：{val}")
    else:
        draft_lines = format_scope_draft_display_lines(
            interrupt_payload.get("scope_draft")
            if isinstance(interrupt_payload.get("scope_draft"), dict)
            else None
        )
        if draft_lines:
            lines.append("当前解析：")
            lines.extend(f"  · {ln}" for ln in draft_lines)
    relaxed = interrupt_payload.get("scope_relaxed_fields") or []
    if relaxed:
        lines.append(f"已自动放宽字段：{'、'.join(str(x) for x in relaxed if str(x).strip())}")
    return "\n".join(lines)


def format_scope_hitl_user_message(*, action: str, payload: dict[str, Any] | None) -> str:
    """将 scope HITL resume 请求格式化为可写入会话历史的 user 正文。"""
    payload = payload or {}
    act = (action or "confirm_scope").strip()
    if act == "abort":
        reason = str(payload.get("reason") or "").strip()
        return f"【取消台账确认】{reason}" if reason else "【取消台账确认】"
    parts: list[str] = []
    supplement = str(payload.get("user_supplement") or "").strip()
    if supplement:
        parts.append(supplement)
    patch = payload.get("scope_patch")
    if isinstance(patch, dict) and patch:
        patch_display = scope_draft_to_display(normalize_scope_patch_keys(patch))
        if patch_display:
            parts.append(
                "修改台账："
                + "；".join(f"{k}：{v}" for k, v in patch_display.items())
            )
    if act == "confirm_scope":
        prefix = "【确认台账】"
    elif act == "edit_scope":
        prefix = "【补充台账】"
    else:
        prefix = f"【{act}】"
    if not parts:
        if act == "confirm_scope":
            return "【确认上述台账信息】"
        if act == "edit_scope":
            return "【修改台账信息】"
        return prefix
    return prefix + "\n" + "\n".join(parts)


def normalize_scope_patch_keys(patch: dict[str, Any] | None) -> dict[str, Any]:
    """将前端可能提交的中文键 scope_patch 归一化为英文字段名。"""
    if not patch:
        return {}
    normalized: dict[str, Any] = {}
    for key, val in patch.items():
        en = SCOPE_FIELD_LABELS_CN_TO_EN.get(str(key), str(key))
        if en == "piperow_name":
            en = "check_location_name"
        normalized[en] = val
    return normalized
