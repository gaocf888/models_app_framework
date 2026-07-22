"""看图诊断 scope 人机协同：台账字段中文展示映射。"""

from __future__ import annotations

from typing import Any

from app.llm.graphs.img_diag_vision_display import VISION_REJECT_INTERRUPT_REASON

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
SCOPE_HITL_SECTION_HEADING = "## 台账信息"

SCOPE_HITL_DB_NOT_MATCHED_PROMPT = (
    "业务库中未匹配到下面台账信息，请确认机组、受热面、检测位置、排数、管数是否准确"
)

SCOPE_HITL_DB_MATCHED_PROMPT = (
    "以下为解析且业务库匹配成功的台账信息，请确认是否准确"
)

SCOPE_HITL_NOT_PARSED_PROMPT = "未识别解析到台账信息，请补充！"

SCOPE_HITL_IMAGE_ONLY_REPLY_EXAMPLE = "1号锅炉水冷壁螺旋段前墙吹灰孔7"
 
SCOPE_HITL_RELAXED_PROMPT = (
    "业务库未匹配到最细粒度范围，系统已自动放宽范围条件后继续查询；"
    "请确认下列台账信息，或修改后重新提交"
)

# vision_first 首轮台账已通过、仅待用户看完图像后继续（pending_vision_user_ack）
HITL_MODE_VISION_ACK_ONLY = "vision_ack_only"
# 库未匹配多轮校正后：失败层候选点选
HITL_MODE_SCOPE_CANDIDATE_PICK = "scope_candidate_pick"

SCOPE_HITL_CANDIDATE_PICK_PROMPT = (
    "业务库仍未匹配到台账信息。请从下列推荐选项中选择最接近的一项，"
    "或继续用自然语言补充修正。"
)

VISION_ACK_CONTINUE_UI_BUTTON: dict[str, Any] = {
    "id": "continue",
    "label": "继续",
    "variant": "primary",
    "action": "confirm_scope",
    "payload": {},
    "requires_input": False,
}


def is_vision_ack_only_hitl(payload: dict[str, Any] | None) -> bool:
    """interrupt / SSE 是否为「仅视觉确认后继续」场景。"""
    if not isinstance(payload, dict):
        return False
    return str(payload.get("hitl_mode") or "") == HITL_MODE_VISION_ACK_ONLY


def is_scope_candidate_pick_hitl(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return str(payload.get("hitl_mode") or "") == HITL_MODE_SCOPE_CANDIDATE_PICK


def build_scope_candidate_pick_ui_buttons(
    suggestions: list[dict[str, Any]] | None,
    *,
    failed_field: str,
) -> list[dict[str, Any]]:
    """前端点选：每项按钮带 scope_patch，选中后 confirm_scope 回写失败字段。"""
    buttons: list[dict[str, Any]] = []
    for item in suggestions or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if failed_field in ("row_no", "tube_no"):
            try:
                patch_val: Any = int(value)
            except (TypeError, ValueError):
                patch_val = value
        else:
            patch_val = str(value).strip()
        cid = str(item.get("id") or len(buttons) + 1)
        label = str(item.get("label") or value).strip()
        rank = item.get("rank")
        if rank is not None:
            label = f"{rank}. {label}"
        buttons.append(
            {
                "id": f"pick_{cid}",
                "label": label,
                "variant": "secondary",
                "action": "confirm_scope",
                "payload": {
                    "scope_patch": {failed_field: patch_val},
                    "candidate_pick_id": cid,
                    "candidate_pick_value": patch_val,
                },
                "requires_input": False,
            }
        )
    return buttons


def apply_scope_candidate_pick_hitl_ui_from_state(
    state: dict[str, Any] | None,
    payload: dict[str, Any],
) -> None:
    """库未匹配候选选择：写入 hitl_mode / ui_buttons / 候选列表字段。"""
    if not isinstance(state, dict):
        return
    if not state.get("pending_scope_candidate_pick"):
        return
    failed_field = str(state.get("scope_candidate_failed_field") or "").strip()
    if not failed_field:
        return
    suggestions = state.get("scope_candidate_suggestions")
    if not isinstance(suggestions, list):
        suggestions = []
    candidates = state.get("scope_candidates")
    if not isinstance(candidates, list):
        candidates = []
    diagnose = state.get("scope_diagnose")
    if not isinstance(diagnose, dict):
        diagnose = {}

    payload["hitl_mode"] = HITL_MODE_SCOPE_CANDIDATE_PICK
    payload["failed_field"] = failed_field
    payload["failed_field_label"] = SCOPE_FIELD_LABELS.get(failed_field, failed_field)
    payload["matched_prefix"] = diagnose.get("matched_prefix") or state.get(
        "scope_candidate_matched_prefix"
    ) or {}
    payload["user_value"] = diagnose.get("user_value")
    payload["candidates"] = candidates
    payload["llm_suggestions"] = suggestions
    payload["ui_buttons"] = build_scope_candidate_pick_ui_buttons(
        suggestions or candidates[:5],
        failed_field=failed_field,
    )
    if not payload.get("prompt"):
        payload["prompt"] = SCOPE_HITL_CANDIDATE_PICK_PROMPT


def apply_vision_ack_only_hitl_ui_from_state(
    state: dict[str, Any] | None,
    payload: dict[str, Any],
) -> None:
    """
    台账库表已内部通过、pending_vision_user_ack：为前端下发「继续」按钮标识。
    其他 HITL 场景不写入 hitl_mode / ui_buttons。
    """
    if not isinstance(state, dict) or not state.get("pending_vision_user_ack"):
        return
    if bool(payload.get("vision_confirm_blocked")):
        return
    if payload.get("include_scope_confirm_preview") is not False:
        return
    if not bool(payload.get("include_vision_preview")):
        return
    payload["hitl_mode"] = HITL_MODE_VISION_ACK_ONLY
    payload["ui_buttons"] = [dict(VISION_ACK_CONTINUE_UI_BUTTON)]


def record_scope_hitl_context(state: dict[str, Any], *, reason: str, prompt: str) -> None:
    """记录当前台账 HITL 场景，供视觉拒识 overlay 清除后恢复。"""
    from app.llm.graphs.img_diag_vision_display import VISION_HITL_REUPLOAD_PROMPT

    if reason and reason != VISION_REJECT_INTERRUPT_REASON:
        state["scope_interrupt_reason"] = reason
    if prompt and prompt != VISION_HITL_REUPLOAD_PROMPT:
        state["scope_hitl_prompt"] = prompt


def resolve_scope_hitl_display_prompt(
    *,
    state: dict[str, Any] | None = None,
    interrupt_payload: dict[str, Any] | None = None,
) -> str:
    """
    台账 HITL 区展示文案：仅 scope 解析/校验结果，不含视觉换图提示。
    视觉换图提示只在「图像可见分析」区展示。
    """
    from app.llm.graphs.img_diag_vision_display import VISION_HITL_REUPLOAD_PROMPT

    src: dict[str, Any] = interrupt_payload if interrupt_payload is not None else (state or {})

    if str(src.get("hitl_mode") or "") == HITL_MODE_SCOPE_CANDIDATE_PICK:
        return SCOPE_HITL_CANDIDATE_PICK_PROMPT
    if isinstance(state, dict) and state.get("pending_scope_candidate_pick"):
        return SCOPE_HITL_CANDIDATE_PICK_PROMPT

    scope_prompt = str(src.get("scope_hitl_prompt") or "").strip()
    if scope_prompt and scope_prompt != VISION_HITL_REUPLOAD_PROMPT:
        return scope_prompt

    human = str(src.get("human_prompt") or src.get("prompt") or "").strip()
    if human and human not in (VISION_HITL_REUPLOAD_PROMPT,) and "请重新上传后再确认台账" not in human:
        return human

    scope_reason = str(src.get("scope_interrupt_reason") or "").strip()
    if scope_reason == "db_validate_matched" or src.get("pending_matched_confirm"):
        return SCOPE_HITL_DB_MATCHED_PROMPT
    if scope_reason == "db_validate_zero_rows":
        return SCOPE_HITL_DB_NOT_MATCHED_PROMPT
    if scope_reason.startswith("missing:"):
        return SCOPE_HITL_NOT_PARSED_PROMPT

    interrupt = str(src.get("interrupt_reason") or "").strip()
    if interrupt == "db_validate_matched":
        return SCOPE_HITL_DB_MATCHED_PROMPT
    if interrupt == "db_validate_zero_rows":
        return SCOPE_HITL_DB_NOT_MATCHED_PROMPT
    if interrupt.startswith("missing:"):
        return SCOPE_HITL_NOT_PARSED_PROMPT

    if src.get("validation_error"):
        return SCOPE_HITL_DB_NOT_MATCHED_PROMPT

    return human or "请补充或确认机组与受热面信息"


def sync_scope_hitl_after_vision_accepted(state: dict[str, Any]) -> None:
    """
    视觉已通过时撤销「非锅炉图」overlay，恢复台账 HITL 的 prompt / reason。
    解决换图后 vision 正常但 human_prompt 仍停留在拒识文案的问题。
    """
    from app.llm.graphs.img_diag_vision_display import (
        VISION_HITL_REUPLOAD_PROMPT,
        is_scope_confirm_blocked_by_vision,
    )

    req = state.get("img_diag_request") if isinstance(state.get("img_diag_request"), dict) else {}
    subtype = str(state.get("img_diag_subtype") or req.get("img_diag_subtype") or "defect_ident")
    vision_data = state.get("vision_prefetch_data")
    if is_scope_confirm_blocked_by_vision(
        vision_data if isinstance(vision_data, dict) else None,
        img_diag_request=req,
        img_diag_subtype=subtype,
    ):
        return

    scope_reason = str(state.get("scope_interrupt_reason") or "").strip()
    scope_prompt = str(state.get("scope_hitl_prompt") or "").strip()

    if state.get("interrupt_reason") == VISION_REJECT_INTERRUPT_REASON:
        state.pop("interrupt_reason", None)
    if state.get("human_prompt") == VISION_HITL_REUPLOAD_PROMPT:
        state.pop("human_prompt", None)

    if scope_reason == "db_validate_matched" or scope_prompt == SCOPE_HITL_DB_MATCHED_PROMPT:
        state["human_prompt"] = SCOPE_HITL_DB_MATCHED_PROMPT
        state["interrupt_reason"] = "db_validate_matched"
        state["pending_matched_confirm"] = True
        state["scope_interrupt_reason"] = "db_validate_matched"
        state["scope_hitl_prompt"] = SCOPE_HITL_DB_MATCHED_PROMPT
        return

    if scope_reason == "db_validate_zero_rows" or scope_prompt == SCOPE_HITL_DB_NOT_MATCHED_PROMPT:
        state["human_prompt"] = SCOPE_HITL_DB_NOT_MATCHED_PROMPT
        state["interrupt_reason"] = "db_validate_zero_rows"
        return

    if scope_prompt == SCOPE_HITL_NOT_PARSED_PROMPT or scope_reason.startswith("missing:"):
        if scope_prompt:
            state["human_prompt"] = scope_prompt
        if scope_reason:
            state["interrupt_reason"] = scope_reason
        return

    if scope_prompt:
        state["human_prompt"] = scope_prompt
    if scope_reason:
        state["interrupt_reason"] = scope_reason

    state.pop("vision_confirm_blocked", None)


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


def _initial_image_only_request(src: dict[str, Any]) -> bool:
    """首请求仅传图、尚未在 query 中补充台账文本。"""
    if src.get("initial_query_empty"):
        return True
    req = src.get("img_diag_request") if isinstance(src.get("img_diag_request"), dict) else {}
    query = str(src.get("query") or req.get("query") or "").strip()
    if query:
        return False
    from app.llm.graphs.img_diag_hitl_images import normalize_image_url_list

    return bool(normalize_image_url_list(req.get("image_urls")))


def is_image_only_initial_scope_hitl(interrupt_payload: dict[str, Any] | None) -> bool:
    """首请求仅传图、尚未补充 query/台账文本时的 HITL 展示分支。"""
    payload = interrupt_payload or {}
    if not _initial_image_only_request(payload):
        return False
    if str(payload.get("scope_cumulative_text") or "").strip():
        return False
    if payload.get("pending_matched_confirm"):
        return False
    scope_reason = str(payload.get("scope_interrupt_reason") or "").strip()
    if scope_reason in ("db_validate_matched", "db_validate_zero_rows"):
        return False
    interrupt = str(payload.get("interrupt_reason") or "").strip()
    if interrupt in ("db_validate_matched", "db_validate_zero_rows"):
        return False
    if payload.get("validation_error"):
        return False
    scope_prompt = str(payload.get("scope_hitl_prompt") or "").strip()
    display_prompt = resolve_scope_hitl_display_prompt(interrupt_payload=payload)
    if scope_prompt == SCOPE_HITL_NOT_PARSED_PROMPT or display_prompt == SCOPE_HITL_NOT_PARSED_PROMPT:
        return True
    if scope_reason.startswith("missing:") or interrupt.startswith("missing:"):
        return True
    if interrupt == VISION_REJECT_INTERRUPT_REASON and (
        scope_reason.startswith("missing:") or scope_prompt == SCOPE_HITL_NOT_PARSED_PROMPT
    ):
        return True
    return False


def build_scope_hitl_confirm_reply_example(interrupt_payload: dict[str, Any] | None) -> str:
    """根据台账 HITL 场景生成确认回复示例（与视觉换图无关）。"""
    payload = interrupt_payload or {}
    if is_image_only_initial_scope_hitl(payload):
        return SCOPE_HITL_IMAGE_ONLY_REPLY_EXAMPLE

    missing = [str(x).strip() for x in (payload.get("missing_fields") or []) if str(x).strip()]

    scope_reason = str(payload.get("scope_interrupt_reason") or "").strip()
    scope_prompt = str(payload.get("scope_hitl_prompt") or "").strip()
    display_prompt = resolve_scope_hitl_display_prompt(interrupt_payload=payload)

    if payload.get("pending_matched_confirm"):
        return "确认或继续"

    if (
        scope_reason == "db_validate_matched"
        or scope_prompt == SCOPE_HITL_DB_MATCHED_PROMPT
        or display_prompt == SCOPE_HITL_DB_MATCHED_PROMPT
    ):
        return "确认或继续"

    if (
        scope_reason == "db_validate_zero_rows"
        or scope_prompt == SCOPE_HITL_DB_NOT_MATCHED_PROMPT
        or display_prompt == SCOPE_HITL_DB_NOT_MATCHED_PROMPT
        or payload.get("validation_error")
    ):
        return "受热面应为****，检测位置应为****"

    if (
        scope_prompt == SCOPE_HITL_NOT_PARSED_PROMPT
        or display_prompt == SCOPE_HITL_NOT_PARSED_PROMPT
        or scope_reason.startswith("missing:")
    ):
        if missing:
            return "，".join(f"{field}应为****" for field in missing[:4])
        return "机组应为****，受热面应为****"

    return "受热面应为****，检测位置应为****"


def _scope_hitl_markdown_bullet(label: str, value: Any) -> str:
    return f"- **{label}**：{value}"


def format_scope_hitl_assistant_message(interrupt_payload: dict[str, Any] | None) -> str:
    """将 scope HITL interrupt 载荷格式化为可写入会话历史的 assistant 正文（Markdown）。"""
    if not interrupt_payload:
        return f"{SCOPE_HITL_TITLE}\n{SCOPE_HITL_SECTION_HEADING}"
    if interrupt_payload.get("include_scope_confirm_preview") is False:
        return ""
    if is_image_only_initial_scope_hitl(interrupt_payload):
        lines: list[str] = [SCOPE_HITL_TITLE, SCOPE_HITL_SECTION_HEADING]
        prompt = resolve_scope_hitl_display_prompt(interrupt_payload=interrupt_payload)
        if prompt:
            lines.append(prompt)
        example = build_scope_hitl_confirm_reply_example(interrupt_payload)
        if example:
            lines.extend(["", "**回复示例**", example])
        return "\n".join(lines)
    lines: list[str] = [SCOPE_HITL_TITLE, SCOPE_HITL_SECTION_HEADING]
    prompt = resolve_scope_hitl_display_prompt(interrupt_payload=interrupt_payload)
    if prompt:
        lines.append(prompt)
    missing = interrupt_payload.get("missing_fields") or []
    if missing:
        lines.append(f"**待补充**：{'、'.join(str(x) for x in missing if str(x).strip())}")
    display = interrupt_payload.get("scope_draft_display")
    if isinstance(display, dict) and display:
        lines.append("")
        lines.append("**当前解析**")
        for key, val in display.items():
            if val is None or (isinstance(val, str) and not val.strip()):
                continue
            lines.append(_scope_hitl_markdown_bullet(str(key), val))
    else:
        draft_display = scope_draft_to_display(
            interrupt_payload.get("scope_draft")
            if isinstance(interrupt_payload.get("scope_draft"), dict)
            else None
        )
        if draft_display:
            lines.append("")
            lines.append("**当前解析**")
            for key, val in draft_display.items():
                lines.append(_scope_hitl_markdown_bullet(str(key), val))
    relaxed = interrupt_payload.get("scope_relaxed_fields") or []
    if relaxed:
        lines.append(f"**已自动放宽字段**：{'、'.join(str(x) for x in relaxed if str(x).strip())}")
    if is_scope_candidate_pick_hitl(interrupt_payload):
        field_label = str(
            interrupt_payload.get("failed_field_label")
            or interrupt_payload.get("failed_field")
            or "台账字段"
        )
        user_val = interrupt_payload.get("user_value")
        lines.extend(["", f"**待选择（{field_label}）**"])
        if user_val is not None and str(user_val).strip():
            lines.append(f"原解析值：{user_val}")
        suggestions = interrupt_payload.get("llm_suggestions") or []
        if isinstance(suggestions, list) and suggestions:
            lines.append("推荐选项：")
            for item in suggestions:
                if not isinstance(item, dict):
                    continue
                val = item.get("value")
                if val is None or (isinstance(val, str) and not str(val).strip()):
                    continue
                rank = item.get("rank")
                reason = str(item.get("reason") or "").strip()
                prefix = f"{rank}. " if rank is not None else "- "
                line = f"{prefix}{val}"
                if reason:
                    line = f"{line}（{reason}）"
                lines.append(line)
        lines.append("请点击上方选项，或继续用自然语言补充修正。")
    example = str(interrupt_payload.get("confirm_reply_example") or "").strip()
    if not example:
        example = build_scope_hitl_confirm_reply_example(interrupt_payload)
    if example and not is_scope_candidate_pick_hitl(interrupt_payload):
        lines.extend(["", "**确认回复示例**", example])
    elif example and is_scope_candidate_pick_hitl(interrupt_payload):
        lines.extend(["", "**也可文字回复**", example])
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
    image_urls = payload.get("image_urls")
    if isinstance(image_urls, list):
        urls = [u.strip() for u in image_urls if isinstance(u, str) and u.strip()]
        if urls:
            parts.append("更换图片：\n" + "\n".join(urls))
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
