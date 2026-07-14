"""看图诊断 scope HITL LangGraph 状态与编排。"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable, Literal, TypedDict

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.graphs.img_diag_checkpoint import (
    build_img_diag_checkpointer,
    img_diag_graph_configurable,
)
from app.llm.graphs.img_diag_scope_affirmation import (
    has_scope_correction_patch,
    is_matched_confirm_affirmative_response,
)
from app.llm.graphs.img_diag_scope_display import (
    SCOPE_HITL_DB_MATCHED_PROMPT,
    SCOPE_HITL_DB_NOT_MATCHED_PROMPT,
    SCOPE_HITL_NOT_PARSED_PROMPT,
    build_scope_hitl_confirm_reply_example,
    format_scope_hitl_assistant_message,
    is_image_only_initial_scope_hitl,
    normalize_scope_patch_keys,
    record_scope_hitl_context,
    resolve_scope_hitl_display_prompt,
    scope_draft_to_display,
    scope_field_label,
    sync_scope_hitl_after_vision_accepted,
)
from app.llm.graphs.img_diag_hitl_images import (
    merge_hitl_image_urls_into_request,
    normalize_image_url_list,
    validate_hitl_image_urls_for_subtype,
)
from app.llm.graphs.img_diag_scope_exclusions import (
    detect_scope_field_exclusions_from_patch,
    detect_scope_field_exclusions_from_text,
    merge_scope_field_exclusions,
)
from app.llm.graphs.img_diag_scope_intent import (
    ImgDiagScopeDraft,
    apply_scope_field_exclusions_to_draft,
    apply_scope_patch,
    build_scope_intent_text,
    confirmed_scope_from_draft,
    draft_from_scope_dict,
    missing_required_scope_fields,
    parse_img_diag_scope_draft,
    should_trigger_scope_hitl,
)
from app.llm.graphs.img_diag_scope_validate import validate_scope_with_relaxation
from app.llm.graphs.img_diag_session_store import (
    create_img_diag_resume_token,
    delete_img_diag_resume_session,
    get_img_diag_resume_session,
)
from app.llm.graphs.img_diag_vision_display import (
    VISION_HITL_REUPLOAD_PROMPT,
    VISION_REJECT_INTERRUPT_REASON,
    apply_vision_rejection_scope_gate,
    build_vision_findings_display,
    format_vision_hitl_assistant_block,
    is_scope_confirm_blocked_by_vision,
)
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)

VisionRefreshFn = Callable[[dict[str, Any]], Awaitable[tuple[dict[str, Any], int, str]]]


class ImgDiagScopeGraphState(TypedDict, total=False):
    request_id: str
    user_id: str
    session_id: str
    analysis_type: str
    img_diag_subtype: str
    query: str
    options: dict[str, Any]
    img_diag_request: dict[str, Any]
    scope_cumulative_text: str
    scope_draft: dict[str, Any]
    scope_confidence: str
    scope_parse_attempts: int
    hitl_rounds: int
    validation_error: str | None
    needs_db_retry: bool
    abort_requested: bool
    abort_reason: str | None
    confirmed_scope_intent: dict[str, Any]
    scope_intent_text: str
    human_interactions: list[dict[str, Any]]
    scope_relaxed_fields: list[str]
    scope_field_exclusions: list[str]
    pending_matched_confirm: bool
    human_prompt: str
    human_suggested_actions: list[str]
    missing_fields: list[str]
    interrupt_reason: str
    orchestrator_path: str
    vision_prefetch_data: dict[str, Any]
    vision_prefetch_ms: int
    vision_prefetch_status: str
    vision_images_replaced: bool
    vision_prefetch_resume_refreshed: bool
    vision_confirm_blocked: bool
    scope_interrupt_reason: str
    scope_hitl_prompt: str
    initial_query_empty: bool
    scope_correction_pending_reparse: bool
    scope_correction_epoch: int
    scope_correction_parsed_epoch: int
    scope_pending_reparse_supplement: str
    vision_hitl_preview_delivered: bool
    pending_vision_user_ack: bool


_SCOPE_CHECKPOINT_PRIORITY_KEYS: frozenset[str] = frozenset(
    {
        "scope_cumulative_text",
        "scope_draft",
        "scope_correction_epoch",
        "scope_correction_parsed_epoch",
        "scope_correction_pending_reparse",
        "scope_pending_reparse_supplement",
        "scope_confidence",
        "missing_fields",
        "pending_matched_confirm",
        "needs_db_retry",
        "validation_error",
        "human_prompt",
        "interrupt_reason",
        "scope_relaxed_fields",
        "confirmed_scope_intent",
        "scope_intent_text",
        "vision_hitl_preview_delivered",
        "pending_vision_user_ack",
    }
)


def _sync_scope_human_confirm_hitl_gate_flags(
    state: ImgDiagScopeGraphState,
    *,
    interrupt_payload: dict[str, Any] | None = None,
) -> None:
    """
    scope_human_confirm 在 interrupt 前会递增 hitl；LangGraph 暂停时未必写回 checkpoint。
    「图像可见分析」已交付标记仅在本轮 interrupt 实际附带且视觉门禁通过时置位
    （拒识展示不计入已交付，以便后续首次换正确图仍能返回一次）。
    """
    state["hitl_rounds"] = int(state.get("hitl_rounds") or 0) + 1
    if not isinstance(interrupt_payload, dict):
        return
    if not bool(interrupt_payload.get("include_vision_preview")):
        return
    if _vision_hitl_gate_blocked(state):
        return
    state["vision_hitl_preview_delivered"] = True


def _scope_hitl_gate_passed(state: dict[str, Any]) -> bool:
    return bool(state.get("confirmed_scope_intent") and state.get("scope_intent_text"))


def _vision_hitl_gate_blocked(state: dict[str, Any]) -> bool:
    return bool(
        state.get("vision_confirm_blocked")
        or str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON
        or _should_block_scope_confirm_by_vision_state(state)
    )


def _needs_vision_user_ack_after_scope_db_match(state: ImgDiagScopeGraphState) -> bool:
    """vision_first 首轮库表命中且视觉通过：台账/视觉双门禁已放行，不再 interrupt 等用户确认。"""
    return False


def _should_include_scope_confirm_in_hitl(state: dict[str, Any]) -> bool:
    """台账门禁已通过时不向用户展示台账确认块；仅未通过时展示。"""
    if state.get("pending_vision_user_ack"):
        return False
    if _scope_hitl_gate_passed(state):
        return False
    if _vision_hitl_gate_blocked(state) and not state.get("needs_db_retry"):
        missing = state.get("missing_fields") or []
        if not missing and not state.get("validation_error"):
            return False
    return True


def _should_include_vision_preview_in_hitl(state: ImgDiagScopeGraphState) -> bool:
    """
    HITL 是否附带「图像可见分析」：
    - 视觉拒识：返回（拒识文案）；不记为「已通过展示」；
    - 视觉已通过且台账未通过：无论此前多少轮错图，首次通过时返回一次；
    - 已返回过通过态分析且未换图：不再返回；
    - 换图后重置「已交付」，新图门禁通过时再返回一次；
    - 双门禁都通过：直接放行，不依赖此处。
    """
    orchestrator_path = str(state.get("orchestrator_path") or "scope_first")
    vision_data = state.get("vision_prefetch_data")
    if not isinstance(vision_data, dict) or not vision_data:
        return False

    images_replaced = bool(state.get("vision_images_replaced"))
    vision_blocked = _vision_hitl_gate_blocked(state)

    if images_replaced:
        state["vision_hitl_preview_delivered"] = False

    if vision_blocked:
        return True
    if orchestrator_path != "vision_first":
        return False
    if _scope_hitl_gate_passed(state):
        return False
    if state.get("vision_hitl_preview_delivered"):
        return False
    return True


def should_emit_img_diag_vision_preview_on_scope_confirmed(
    *,
    orchestrator_path: str,
    vision_prefetch: dict[str, Any] | None,
    vision_hitl_preview_delivered: bool,
) -> bool:
    """视觉可见分析仅在 scope interrupt 路径下发；confirmed 直出不再补发。"""
    return False


def _scope_hitl_result_extras(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "vision_hitl_preview_delivered": bool(state.get("vision_hitl_preview_delivered")),
    }


def _mark_scope_correction_written(state: ImgDiagScopeGraphState) -> None:
    state["scope_correction_epoch"] = int(state.get("scope_correction_epoch") or 0) + 1
    state["scope_correction_pending_reparse"] = True


def _mark_scope_correction_parsed(state: ImgDiagScopeGraphState) -> None:
    pending = str(state.get("scope_pending_reparse_supplement") or "").strip()
    if pending:
        cumulative = str(state.get("scope_cumulative_text") or "").strip()
        if pending not in cumulative:
            return
    state["scope_correction_parsed_epoch"] = int(state.get("scope_correction_epoch") or 0)
    state.pop("scope_correction_pending_reparse", None)
    state.pop("scope_pending_reparse_supplement", None)


def _scope_correction_needs_reparse(state: dict[str, Any]) -> bool:
    if str(state.get("scope_pending_reparse_supplement") or "").strip():
        return True
    if state.get("scope_correction_pending_reparse"):
        return True
    epoch = int(state.get("scope_correction_epoch") or 0)
    parsed = int(state.get("scope_correction_parsed_epoch") or 0)
    return epoch > parsed


def _merge_scope_resume_interrupt_state(
    checkpoint: dict[str, Any],
    graph_delta: dict[str, Any],
) -> dict[str, Any]:
    """Resume 中断态合并：checkpoint（含 prep 持久化）优先于 graph 可能过期的台账字段。"""
    merged = dict(checkpoint)
    merged.update(graph_delta)
    ck_attempts = int(checkpoint.get("scope_parse_attempts") or 0)
    gd_attempts = int(graph_delta.get("scope_parse_attempts") or 0)
    prefer_graph_scope = gd_attempts > ck_attempts
    for key in _SCOPE_CHECKPOINT_PRIORITY_KEYS:
        if prefer_graph_scope and key in graph_delta:
            merged[key] = graph_delta[key]
        elif key in checkpoint:
            merged[key] = checkpoint[key]
    ck_hi = checkpoint.get("human_interactions") or []
    gd_hi = graph_delta.get("human_interactions") or []
    if isinstance(gd_hi, list) and len(gd_hi) > len(ck_hi if isinstance(ck_hi, list) else []):
        merged["human_interactions"] = gd_hi
    if int(graph_delta.get("hitl_rounds") or 0) > int(checkpoint.get("hitl_rounds") or 0):
        merged["hitl_rounds"] = graph_delta["hitl_rounds"]
    merged["scope_parse_attempts"] = max(
        int(checkpoint.get("scope_parse_attempts") or 0),
        int(graph_delta.get("scope_parse_attempts") or 0),
    )
    return merged


def _scope_resume_checkpoint_as_node(snap: Any) -> str | None:
    """图处于 interrupt 时须以 scope_human_confirm 写回 checkpoint，否则 Redis 下更新会丢失。"""
    if snap is not None and getattr(snap, "interrupts", None):
        return "scope_human_confirm"
    return None


def _is_scope_field_correction_line(line: str) -> bool:
    """单行 user_supplement 是否为字段校正（如「检测位置应为…」），而非完整台账描述。"""
    return "应为" in (line or "").strip()


def _scope_cumulative_for_correction_reparse(state: dict[str, Any]) -> str:
    """
    校正重解析专用输入：原始 query + 本轮最新 supplement。
    避免 cumulative 中上一轮错误校正干扰 LLM 解析（不改 prompt）。
    """
    pending = str(state.get("scope_pending_reparse_supplement") or "").strip()
    if not pending:
        return str(state.get("scope_cumulative_text") or state.get("query") or "").strip()
    original = str(state.get("query") or "").strip()
    if not original:
        cumulative = str(state.get("scope_cumulative_text") or "").strip()
        for line in cumulative.splitlines():
            line = line.strip()
            if not line or line == pending or _is_scope_field_correction_line(line):
                continue
            original = line
            break
        if not original:
            return pending
    if original and original != pending:
        return f"{original}\n{pending}".strip()
    return pending


def _cfg():
    return get_app_config().analysis


def _max_hitl_rounds() -> int:
    return max(1, int(getattr(_cfg(), "img_diag_scope_hitl_max_rounds", 5)))


def scope_auto_relax_allowed(*, hitl_rounds: int) -> bool:
    """
    是否允许库表校验失败后自动放宽 scope 细粒度字段。

    默认关闭（ANALYSIS_IMG_DIAG_SCOPE_AUTO_RELAX_ENABLED=false）；
    开启时保持旧行为：至少 2 轮人机后再逐级去掉 tube_no → row_no → check_location_name。
    """
    if not bool(getattr(_cfg(), "img_diag_scope_auto_relax_enabled", False)):
        return False
    return int(hitl_rounds or 0) >= 2


def _scope_matched_confirm_enabled() -> bool:
    return bool(getattr(_cfg(), "img_diag_scope_matched_confirm_enabled", True))


def _leakage_burst_scope_auto_confirm_after_db_match(state: ImgDiagScopeGraphState) -> bool:
    """
    泄爆分析：台账库匹配成功后直接放行，不进入 matched HITL（有图/无图一致）。
    视觉等非台账门禁仍由 apply_vision_rejection_scope_gate 处理。
    """
    return str(state.get("img_diag_subtype") or "defect_ident") == "leakage_burst"


def _draft_from_state(state: ImgDiagScopeGraphState) -> ImgDiagScopeDraft:
    draft_dict = state.get("scope_draft") or {}
    tm = parse_img_diag_scope_draft(state.get("scope_cumulative_text") or "").time_meta
    return draft_from_scope_dict(draft_dict, time_meta=tm)


def _finalize_confirmed_scope(state: ImgDiagScopeGraphState) -> None:
    draft = _draft_from_state(state)
    state["scope_draft"] = draft.to_dict()
    state["confirmed_scope_intent"] = confirmed_scope_from_draft(draft)
    state["scope_intent_text"] = build_scope_intent_text(
        draft,
        scope_question=state.get("scope_cumulative_text") or "",
    )
    state["pending_matched_confirm"] = False
    state.pop("pending_vision_user_ack", None)
    state["needs_db_retry"] = False
    state["validation_error"] = None


def _scope_draft_payload(state: ImgDiagScopeGraphState) -> dict[str, Any]:
    draft = state.get("scope_draft") or {}
    from app.llm.graphs.img_diag_scope_intent import normalize_img_diag_scope_dict

    normalized = normalize_img_diag_scope_dict(draft)
    return {
        "boiler": normalized.get("boiler"),
        "device_name": normalized.get("device_name"),
        "check_location_name": normalized.get("check_location_name"),
        "row_no": normalized.get("row_no"),
        "tube_no": normalized.get("tube_no"),
    }


def _enrich_interrupt_payload_from_state(state: ImgDiagScopeGraphState, payload: dict[str, Any]) -> None:
    orchestrator_path = str(state.get("orchestrator_path") or "scope_first")
    payload["orchestrator_path"] = orchestrator_path
    payload["img_diag_subtype"] = str(state.get("img_diag_subtype") or "defect_ident")
    include_vision = _should_include_vision_preview_in_hitl(state)
    payload["include_vision_preview"] = include_vision
    if include_vision:
        vision_data = state.get("vision_prefetch_data")
        if not _vision_hitl_gate_blocked(state):
            state["vision_hitl_preview_delivered"] = True
        subtype = str(state.get("img_diag_subtype") or "defect_ident")
        payload["vision_findings_display"] = build_vision_findings_display(
            vision_data,
            img_diag_subtype=subtype,
        )
        payload["vision_hitl_assistant_message"] = format_vision_hitl_assistant_block(
            vision_data if isinstance(vision_data, dict) else None,
            img_diag_subtype=subtype,
            include_macro_appearance_heading=True,
        )
    if state.get("initial_query_empty"):
        payload["initial_query_empty"] = True
        payload["scope_cumulative_text"] = str(state.get("scope_cumulative_text") or "").strip()
    payload["confirm_reply_example"] = build_scope_hitl_confirm_reply_example(payload)
    from app.llm.graphs.img_diag_scope_display import apply_vision_ack_only_hitl_ui_from_state

    apply_vision_ack_only_hitl_ui_from_state(state, payload)


def _resume_session_kwargs(
    state: ImgDiagScopeGraphState,
    *,
    thread_id: str,
    request_id: str,
    interrupt_payload: dict[str, Any],
    img_diag_request: dict[str, Any],
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "thread_id": thread_id,
        "request_id": request_id,
        "user_id": str(state.get("user_id") or ""),
        "session_id": str(state.get("session_id") or ""),
        "analysis_type": str(state.get("analysis_type") or ""),
        "img_diag_subtype": str(state.get("img_diag_subtype") or ""),
        "interrupt_payload": interrupt_payload,
        "img_diag_request": img_diag_request,
        "orchestrator_path": str(state.get("orchestrator_path") or "scope_first"),
    }
    vision_data = state.get("vision_prefetch_data")
    if isinstance(vision_data, dict) and vision_data:
        kwargs["vision_prefetch"] = vision_data
        kwargs["vision_prefetch_ms"] = int(state.get("vision_prefetch_ms") or 0)
        kwargs["vision_prefetch_status"] = str(state.get("vision_prefetch_status") or "")
    return kwargs


async def _finalize_state_after_hitl_resume(
    state: dict[str, Any],
    *,
    session: Any,
    vision_refresh: VisionRefreshFn | None,
    for_interrupt: bool,
) -> dict[str, Any]:
    """
    换图后重跑视觉并写回 state；更新 img_diag_request。
    interrupt：Path1/Path2 均刷新 prefetch 供预览；
    confirm：vision_first 且有图时刷新 prefetch，避免 session 中旧视觉结果误阻断。
    """
    updated_request = dict(state.get("img_diag_request") or session.img_diag_request or {})
    state["img_diag_request"] = updated_request
    orchestrator_path = str(state.get("orchestrator_path") or session.orchestrator_path or "scope_first")
    urls = normalize_image_url_list(updated_request.get("image_urls"))
    has_images = bool(urls)

    should_refresh = vision_refresh is not None and has_images and (
        bool(state.get("vision_images_replaced"))
        or bool(state.get("vision_prefetch_resume_refreshed"))
        or for_interrupt
        or (not for_interrupt and orchestrator_path == "vision_first")
    )
    if should_refresh:
        replaced_before_refresh = bool(state.get("vision_images_replaced"))
        vision_data, ms, status = await vision_refresh(updated_request)
        state["vision_prefetch_data"] = vision_data
        state["vision_prefetch_ms"] = int(ms or 0)
        state["vision_prefetch_status"] = str(status or "")
        state["vision_images_replaced"] = False
        state.pop("vision_prefetch_resume_refreshed", None)
        if replaced_before_refresh:
            # 换图后允许「新图首次门禁通过」再展示一次图像可见分析
            state["vision_hitl_preview_delivered"] = False
        sync_scope_hitl_after_vision_accepted(state)
        logger.info(
            "img_diag hitl vision refreshed orchestrator=%s for_interrupt=%s ms=%s status=%s url_count=%s",
            orchestrator_path,
            for_interrupt,
            ms,
            status,
            len(urls),
        )
        return updated_request

    if not state.get("vision_prefetch_data") and session.vision_prefetch:
        if not (orchestrator_path == "vision_first" and has_images):
            state["vision_prefetch_data"] = session.vision_prefetch
            state["vision_prefetch_ms"] = session.vision_prefetch_ms
            state["vision_prefetch_status"] = session.vision_prefetch_status
    return updated_request


def _normalize_human_scope_input(human: dict[str, Any]) -> dict[str, Any]:
    """兼容 resume 时 user_supplement 等字段写在顶层而非 payload 内。"""
    if not isinstance(human, dict):
        return {"action": "confirm_scope", "payload": {}}
    out = dict(human)
    payload = out.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    merged = dict(payload)
    for key in ("user_supplement", "scope_patch", "image_urls", "reason"):
        if key in merged and merged.get(key) not in (None, "", []):
            continue
        top = out.get(key)
        if top not in (None, "", []):
            merged[key] = top
    out["payload"] = merged
    return out


def _merge_scope_supplement_into_cumulative(
    state: ImgDiagScopeGraphState,
    supplement: str,
) -> bool:
    """将 user_supplement 并入 scope_cumulative_text；已存在则跳过。返回是否新写入。"""
    supplement = str(supplement or "").strip()
    if not supplement:
        return False
    cumulative = (state.get("scope_cumulative_text") or state.get("query") or "").strip()
    if supplement in cumulative:
        if str(state.get("scope_pending_reparse_supplement") or "").strip() != supplement:
            state["scope_pending_reparse_supplement"] = supplement
            _mark_scope_correction_written(state)
            return True
        return False
    state["scope_cumulative_text"] = f"{cumulative}\n{supplement}".strip() if cumulative else supplement
    state["scope_pending_reparse_supplement"] = supplement
    _mark_scope_correction_written(state)
    return True


def _scope_supplement_merged_into_cumulative(*, supplement: str, cumulative: str) -> bool:
    if not supplement:
        return True
    if not cumulative:
        return False
    if supplement in cumulative:
        return True
    head = supplement[: min(12, len(supplement))]
    return bool(head and head in cumulative)


def _log_scope_resume_diagnostics(
    *,
    request_id: str,
    action: str,
    payload: dict[str, Any],
    state: dict[str, Any],
    status: str,
    graph_ran: bool,
    still_interrupted: bool = False,
    resume_prep_persisted: bool = False,
) -> None:
    supplement = str(payload.get("user_supplement") or "").strip()
    cumulative = str(state.get("scope_cumulative_text") or "").strip()
    interactions = state.get("human_interactions") or []
    merged = _scope_supplement_merged_into_cumulative(
        supplement=supplement,
        cumulative=cumulative,
    )
    logger.info(
        "img_diag scope resume diagnostic request_id=%s status=%s action=%s "
        "payload_has_supplement=%s supplement_len=%d graph_ran=%s still_interrupted=%s "
        "resume_prep_persisted=%s cumulative_len=%d hitl_rounds=%s interactions=%d "
        "supplement_merged=%s",
        request_id,
        status,
        action,
        bool(supplement),
        len(supplement),
        graph_ran,
        still_interrupted,
        resume_prep_persisted,
        len(cumulative),
        state.get("hitl_rounds"),
        len(interactions),
        merged,
    )
    if supplement and not merged:
        logger.warning(
            "img_diag scope resume: user_supplement not merged into scope_cumulative_text "
            "request_id=%s cumulative_preview=%r supplement_preview=%r graph_ran=%s "
            "still_interrupted=%s interactions=%d",
            request_id,
            cumulative[:160],
            supplement[:160],
            graph_ran,
            still_interrupted,
            len(interactions),
        )


async def _prepare_scope_resume_state(
    graph: Any,
    *,
    thread_id: str,
    session: Any,
    payload: dict[str, Any],
    action: str,
    vision_refresh: VisionRefreshFn | None,
) -> str | None:
    """resume 前合并换图 URL / 台账校正，并在必要时重跑视觉写回 checkpoint。"""
    config = img_diag_graph_configurable(thread_id)
    snap = await graph.aget_state(config)
    if not snap or not snap.values:
        return None
    state = dict(snap.values)
    payload_dict = payload if isinstance(payload, dict) else {}
    human_norm = _normalize_human_scope_input({"action": action, "payload": payload_dict})
    payload_dict = dict(human_norm.get("payload") or {})
    action = str(human_norm.get("action") or action)

    img_err = _apply_hitl_image_urls_to_state(state, payload_dict)
    if img_err:
        return img_err
    req = dict(state.get("img_diag_request") or session.img_diag_request or {})
    state["img_diag_request"] = req

    correction_applied = False
    if _resume_payload_needs_scope_reparse(payload_dict, action=action, state=state):
        correction_applied = _apply_scope_correction_from_payload(
            state,
            payload_dict,
            action=action,
        )

    urls_changed = bool(state.get("vision_images_replaced"))
    # 仅在 URL 实际变更时 prep 重跑视觉；勿因 payload 重复携带 image_urls 而刷新
    should_refresh_vision = vision_refresh is not None and urls_changed
    if should_refresh_vision:
        vision_data, ms, status = await vision_refresh(req)
        state["vision_prefetch_data"] = vision_data
        state["vision_prefetch_ms"] = int(ms or 0)
        state["vision_prefetch_status"] = str(status or "")
        state["vision_prefetch_resume_refreshed"] = True
        state["vision_images_replaced"] = False
        # URL 已变更：清除「通过态图像可见分析已交付」，便于新图首次通过时再返回一次
        state["vision_hitl_preview_delivered"] = False
        sync_scope_hitl_after_vision_accepted(state)

    needs_persist = urls_changed or should_refresh_vision or correction_applied
    if needs_persist:
        as_node = _scope_resume_checkpoint_as_node(snap)
        await graph.aupdate_state(config, state, as_node=as_node)
        logger.info(
            "img_diag scope resume prep persisted request_id=%s urls_changed=%s "
            "vision_refreshed=%s scope_correction=%s interrupted=%s as_node=%s",
            session.request_id,
            urls_changed,
            should_refresh_vision,
            correction_applied,
            bool(getattr(snap, "interrupts", None)),
            as_node,
        )
    return None


async def _try_resolve_vision_user_ack_after_prep(
    graph: Any,
    config: dict[str, Any],
    session: Any,
    *,
    action: str,
    payload: dict[str, Any] | None,
    vision_refresh: VisionRefreshFn | None,
) -> dict[str, Any] | None:
    """
    prep 后「台账已内部通过、待视觉确认」且用户肯定回复时直接完成，避免 graph resume 状态漂移。
    """
    payload = payload or {}
    snap = await graph.aget_state(config)
    if not snap or not snap.values:
        return None
    state: dict[str, Any] = dict(snap.values)
    if not state.get("pending_vision_user_ack"):
        return None
    if _hitl_payload_only_replaced_images(payload):
        return None
    if not is_matched_confirm_affirmative_response(action, payload):
        return None
    if not (state.get("confirmed_scope_intent") and state.get("scope_intent_text")):
        return None

    req = dict(state.get("img_diag_request") or session.img_diag_request or {})
    state["img_diag_request"] = req
    thread_id = session.thread_id
    request_id = session.request_id

    if _should_block_scope_confirm_by_vision_state(state):
        apply_vision_rejection_scope_gate(state)
        await graph.aupdate_state(config, state)
        updated_request = await _finalize_state_after_hitl_resume(
            state,
            session=session,
            vision_refresh=vision_refresh,
            for_interrupt=True,
        )
        return _scope_interrupt_from_state(
            state,
            thread_id=thread_id,
            request_id=request_id,
            img_diag_request=updated_request if isinstance(updated_request, dict) else req,
        )

    state.pop("pending_vision_user_ack", None)
    if str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON:
        state.pop("interrupt_reason", None)
    state.pop("vision_confirm_blocked", None)
    await graph.aupdate_state(config, state)

    state_confirmed = dict(state)
    updated_request = await _finalize_state_after_hitl_resume(
        state_confirmed,
        session=session,
        vision_refresh=vision_refresh,
        for_interrupt=False,
    )
    return {
        "status": "confirmed",
        "request_id": request_id,
        "confirmed_scope_intent": state_confirmed["confirmed_scope_intent"],
        "scope_intent_text": state_confirmed["scope_intent_text"],
        "img_diag_request": updated_request,
        "orchestrator_path": session.orchestrator_path,
        "vision_prefetch": state_confirmed.get("vision_prefetch_data"),
        "vision_prefetch_ms": int(state_confirmed.get("vision_prefetch_ms") or 0),
        "vision_prefetch_status": str(state_confirmed.get("vision_prefetch_status") or ""),
        **_scope_hitl_result_extras(state_confirmed),
    }


async def _try_resolve_matched_confirm_after_prep(
    graph: Any,
    config: dict[str, Any],
    session: Any,
    *,
    action: str,
    payload: dict[str, Any] | None,
    vision_refresh: VisionRefreshFn | None,
) -> dict[str, Any] | None:
    """
    prep 已将视觉/URL 写回 checkpoint 后，对「台账匹配待确认 + 用户肯定回复」直接完成或阻断。
    避免 LangGraph resume 与 prep 状态不一致导致第 3 轮正确图仍被旧视觉误拦。
    返回 None 表示仍需 graph resume（如仅换图、台账修正等）。
    """
    payload = payload or {}
    snap = await graph.aget_state(config)
    if not snap or not snap.values:
        return None
    state: dict[str, Any] = dict(snap.values)
    if not state.get("pending_matched_confirm"):
        return None
    if _hitl_payload_only_replaced_images(payload):
        return None
    if not is_matched_confirm_affirmative_response(action, payload):
        return None

    req = dict(state.get("img_diag_request") or session.img_diag_request or {})
    state["img_diag_request"] = req
    thread_id = session.thread_id
    request_id = session.request_id

    if _should_block_scope_confirm_by_vision_state(state):
        apply_vision_rejection_scope_gate(state)
        await graph.aupdate_state(config, state)
        updated_request = await _finalize_state_after_hitl_resume(
            state,
            session=session,
            vision_refresh=vision_refresh,
            for_interrupt=True,
        )
        return _scope_interrupt_from_state(
            state,
            thread_id=thread_id,
            request_id=request_id,
            img_diag_request=updated_request if isinstance(updated_request, dict) else req,
        )

    _finalize_confirmed_scope(state)
    if str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON:
        state.pop("interrupt_reason", None)
    state.pop("vision_confirm_blocked", None)
    await graph.aupdate_state(config, state)

    state_confirmed = dict(state)
    updated_request = await _finalize_state_after_hitl_resume(
        state_confirmed,
        session=session,
        vision_refresh=vision_refresh,
        for_interrupt=False,
    )
    if _should_block_scope_confirm_by_vision_state(state_confirmed):
        apply_vision_rejection_scope_gate(state_confirmed)
        await graph.aupdate_state(config, state_confirmed)
        return _scope_interrupt_from_state(
            state_confirmed,
            thread_id=thread_id,
            request_id=request_id,
            img_diag_request=updated_request if isinstance(updated_request, dict) else req,
        )

    return {
        "status": "confirmed",
        "request_id": request_id,
        "confirmed_scope_intent": state_confirmed["confirmed_scope_intent"],
        "scope_intent_text": state_confirmed["scope_intent_text"],
        "img_diag_request": updated_request,
        "orchestrator_path": session.orchestrator_path,
        "vision_prefetch": state_confirmed.get("vision_prefetch_data"),
        "vision_prefetch_ms": int(state_confirmed.get("vision_prefetch_ms") or 0),
        "vision_prefetch_status": str(state_confirmed.get("vision_prefetch_status") or ""),
        **_scope_hitl_result_extras(state_confirmed),
    }


def _scope_interrupt_from_state(
    state: dict[str, Any],
    *,
    thread_id: str,
    request_id: str,
    img_diag_request: dict[str, Any],
) -> dict[str, Any]:
    intr = _build_interrupt_payload(state)
    token = create_img_diag_resume_token(
        **_resume_session_kwargs(
            state,
            thread_id=thread_id,
            request_id=request_id,
            interrupt_payload=intr,
            img_diag_request=img_diag_request,
        )
    )
    return {
        "status": "interrupt",
        "request_id": request_id,
        "resume_token": token,
        "interrupt_payload": intr,
        "img_diag_request": state.get("img_diag_request") or img_diag_request,
        "orchestrator_path": str(state.get("orchestrator_path") or "scope_first"),
        "vision_prefetch": state.get("vision_prefetch_data"),
        "vision_prefetch_ms": int(state.get("vision_prefetch_ms") or 0),
        "vision_prefetch_status": str(state.get("vision_prefetch_status") or ""),
    }


def _apply_vision_gate_or_restore_scope_hitl(state: ImgDiagScopeGraphState) -> None:
    """HITL 展示前：非锅炉图阻断；已通过则恢复台账确认文案。"""
    if apply_vision_rejection_scope_gate(state):
        return
    if state.get("pending_vision_user_ack"):
        return
    sync_scope_hitl_after_vision_accepted(state)


def _build_interrupt_payload(state: ImgDiagScopeGraphState) -> dict[str, Any]:
    _apply_vision_gate_or_restore_scope_hitl(state)
    include_scope_confirm = _should_include_scope_confirm_in_hitl(state)
    scope_draft = _scope_draft_payload(state) if include_scope_confirm else {}
    missing = state.get("missing_fields") or [] if include_scope_confirm else []
    relaxed = state.get("scope_relaxed_fields") or [] if include_scope_confirm else []
    vision_blocked = bool(
        state.get("vision_confirm_blocked")
        or str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON
    )
    if include_scope_confirm:
        prompt = resolve_scope_hitl_display_prompt(state=state)
    else:
        from app.llm.graphs.img_diag_vision_display import VISION_HITL_REUPLOAD_PROMPT

        prompt = VISION_HITL_REUPLOAD_PROMPT if vision_blocked else ""
    payload: dict[str, Any] = {
        "prompt": prompt,
        "scope_draft": scope_draft,
        "scope_draft_display": scope_draft_to_display(scope_draft) if include_scope_confirm else {},
        "missing_fields": [scope_field_label(f) for f in missing],
        "validation_error": state.get("validation_error") if include_scope_confirm else None,
        "suggested_actions": state.get("human_suggested_actions")
        or ["confirm_scope", "edit_scope", "abort"],
        "request_id": state.get("request_id"),
        "interrupt_reason": state.get("interrupt_reason"),
        "pending_matched_confirm": bool(state.get("pending_matched_confirm")) if include_scope_confirm else False,
        "vision_confirm_blocked": vision_blocked,
        "include_scope_confirm_preview": include_scope_confirm,
    }
    scope_reason = state.get("scope_interrupt_reason")
    if isinstance(scope_reason, str) and scope_reason.strip():
        payload["scope_interrupt_reason"] = scope_reason.strip()
    scope_prompt = state.get("scope_hitl_prompt")
    if isinstance(scope_prompt, str) and scope_prompt.strip():
        payload["scope_hitl_prompt"] = scope_prompt.strip()
    if relaxed:
        payload["scope_relaxed_fields"] = [scope_field_label(f) for f in relaxed]
    _enrich_interrupt_payload_from_state(state, payload)
    payload["scope_hitl_assistant_message"] = format_scope_hitl_assistant_message(payload)
    payload["scope_reply_example_label"] = (
        "回复示例" if is_image_only_initial_scope_hitl(payload) else "确认回复示例"
    )
    return payload


def _should_block_scope_confirm_by_vision_state(state: ImgDiagScopeGraphState) -> bool:
    """有图且视觉拒识 / 换图后视觉结果不可信时，不得完成台账确认。"""
    from app.llm.graphs.img_diag_vision_display import img_diag_request_has_images

    req = state.get("img_diag_request") if isinstance(state.get("img_diag_request"), dict) else {}
    subtype = str(state.get("img_diag_subtype") or req.get("img_diag_subtype") or "defect_ident")
    vision_data = state.get("vision_prefetch_data")
    if is_scope_confirm_blocked_by_vision(
        vision_data if isinstance(vision_data, dict) else None,
        img_diag_request=req,
        img_diag_subtype=subtype,
    ):
        return True
    if state.get("vision_images_replaced") and img_diag_request_has_images(
        req, img_diag_subtype=subtype
    ):
        if not isinstance(vision_data, dict) or vision_data.get("vision_skipped"):
            return True
    return False


def _last_human_response_needs_scope_reparse(state: dict[str, Any]) -> bool:
    """
    用户本轮 resume 提供了台账补充/修正，须重新 preflight → db_validate。
    视觉拒识 alone 不应阻止该路径（仅换图或 matched 纯肯定确认除外）。
    """
    interactions = state.get("human_interactions") or []
    if not interactions:
        return False
    last = interactions[-1]
    if not isinstance(last, dict):
        return False
    human = _normalize_human_scope_input(last.get("response") or {})
    payload = human.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    action = str(human.get("action") or "confirm_scope")

    if has_scope_correction_patch(payload.get("scope_patch")):
        return True
    if _hitl_payload_only_replaced_images(payload):
        return False

    supplement = str(payload.get("user_supplement") or "").strip()
    if not supplement:
        return False
    if state.get("pending_matched_confirm") and is_matched_confirm_affirmative_response(
        action, payload
    ):
        return False
    return True


def _state_needs_scope_hitl_interrupt(state: dict[str, Any]) -> bool:
    """图已结束但尚未 confirmed，仍须下一次人机协同（视觉/台账待确认）。"""
    if state.get("pending_vision_user_ack"):
        return True
    if state.get("confirmed_scope_intent") and state.get("scope_intent_text"):
        return _vision_hitl_gate_blocked(state)
    if state.get("pending_matched_confirm"):
        return True
    if state.get("vision_confirm_blocked"):
        return True
    if str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON:
        return True
    if state.get("needs_db_retry"):
        return True
    return False


def _hitl_payload_only_replaced_images(payload: dict[str, Any] | None) -> bool:
    """resume 仅换图、无台账补充/修正时不视为台账确认。"""
    payload = payload or {}
    urls = payload.get("image_urls")
    has_urls = isinstance(urls, list) and any(isinstance(u, str) and u.strip() for u in urls)
    if not has_urls:
        return False
    if str(payload.get("user_supplement") or "").strip():
        return False
    return not has_scope_correction_patch(payload.get("scope_patch"))


def _resume_payload_needs_scope_reparse(
    payload: dict[str, Any] | None,
    *,
    action: str,
    state: dict[str, Any],
) -> bool:
    """Resume 请求是否携带须重新 preflight→db_validate 的台账补充/修正。"""
    payload = payload or {}
    if has_scope_correction_patch(payload.get("scope_patch")):
        return True
    if _hitl_payload_only_replaced_images(payload):
        return False
    supplement = str(payload.get("user_supplement") or "").strip()
    if not supplement:
        return False
    if state.get("pending_matched_confirm") and is_matched_confirm_affirmative_response(
        action, payload
    ):
        return False
    if state.get("pending_vision_user_ack") and is_matched_confirm_affirmative_response(
        action, payload
    ):
        return False
    return True


def _apply_scope_correction_from_payload(
    state: ImgDiagScopeGraphState,
    payload: dict[str, Any],
    *,
    action: str,
) -> bool:
    """合并 user_supplement/scope_patch；返回是否写入了台账修正。"""
    payload = payload if isinstance(payload, dict) else {}
    pending_matched = bool(state.get("pending_matched_confirm"))
    pending_vision_ack = bool(state.get("pending_vision_user_ack"))
    affirmative = pending_matched and is_matched_confirm_affirmative_response(action, payload)
    affirmative_vision = pending_vision_ack and is_matched_confirm_affirmative_response(
        action, payload
    )
    if affirmative or affirmative_vision:
        return False

    changed = False
    correction_changed = False
    supplement = str(payload.get("user_supplement") or "").strip()
    patch = payload.get("scope_patch")

    new_excluded: set[str] = set()
    if supplement:
        new_excluded |= set(detect_scope_field_exclusions_from_text(supplement))
    if isinstance(patch, dict):
        patch = normalize_scope_patch_keys(patch)
        new_excluded |= set(detect_scope_field_exclusions_from_patch(patch))
    if new_excluded:
        state["scope_field_exclusions"] = merge_scope_field_exclusions(
            state.get("scope_field_exclusions"),
            frozenset(new_excluded),
        )
        changed = True
    if supplement and _merge_scope_supplement_into_cumulative(state, supplement):
        changed = True
        correction_changed = True
    if isinstance(patch, dict) and patch and state.get("scope_draft"):
        dd = state["scope_draft"]
        tm = parse_img_diag_scope_draft(state.get("scope_cumulative_text") or "").time_meta
        draft = draft_from_scope_dict(dd, time_meta=tm)
        draft = apply_scope_patch(draft, patch)
        excluded = frozenset(state.get("scope_field_exclusions") or ())
        state["scope_draft"] = apply_scope_field_exclusions_to_draft(draft, excluded).to_dict()
        changed = True
        correction_changed = True
    elif state.get("scope_draft") and state.get("scope_field_exclusions"):
        dd = state["scope_draft"]
        tm = parse_img_diag_scope_draft(state.get("scope_cumulative_text") or "").time_meta
        draft = draft_from_scope_dict(dd, time_meta=tm)
        excluded = frozenset(state.get("scope_field_exclusions") or ())
        state["scope_draft"] = apply_scope_field_exclusions_to_draft(draft, excluded).to_dict()
        changed = True

    if (pending_matched or pending_vision_ack) and not (affirmative or affirmative_vision) and correction_changed:
        state["pending_matched_confirm"] = False
        state.pop("pending_vision_user_ack", None)
        state.pop("confirmed_scope_intent", None)
        state.pop("scope_intent_text", None)
        changed = True

    if changed and not _scope_correction_needs_reparse(state):
        _mark_scope_correction_written(state)
    if changed:
        state["needs_db_retry"] = False
        state["validation_error"] = None
    return changed


async def _run_scope_reparse_pipeline(
    state: ImgDiagScopeGraphState,
    *,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
) -> ImgDiagScopeGraphState:
    """对 state 执行 scope_preflight_llm → scope_db_validate（resume 校正兜底）。"""
    nodes = make_img_diag_scope_nodes(
        llm_client=llm_client,
        prompt_registry=prompt_registry,
    )
    full_cumulative = str(state.get("scope_cumulative_text") or state.get("query") or "").strip()
    parse_cumulative = _scope_cumulative_for_correction_reparse(state)
    parse_input = dict(state)
    parse_input["scope_cumulative_text"] = parse_cumulative
    logger.info(
        "img_diag scope correction reparse request_id=%s parse_cumulative_len=%d "
        "full_cumulative_len=%d pending_supplement=%s",
        state.get("request_id"),
        len(parse_cumulative),
        len(full_cumulative),
        bool(state.get("scope_pending_reparse_supplement")),
    )
    state = await nodes["scope_preflight_llm"](parse_input)
    state["scope_cumulative_text"] = full_cumulative
    state = await nodes["scope_db_validate"](state)
    _mark_scope_correction_parsed(state)
    return state


def _apply_hitl_image_urls_to_state(
    state: ImgDiagScopeGraphState,
    payload: dict[str, Any],
) -> str | None:
    """先合并 URL，再 scope 解析。返回校验错误说明或 None。"""
    state["vision_images_replaced"] = False
    req = dict(state.get("img_diag_request") or {})
    updated, changed = merge_hitl_image_urls_into_request(req, payload)
    if not changed:
        return None
    subtype = str(state.get("img_diag_subtype") or updated.get("img_diag_subtype") or "defect_ident")
    err = validate_hitl_image_urls_for_subtype(
        img_diag_subtype=subtype,
        image_urls=list(updated.get("image_urls") or []),
    )
    if err:
        return err
    state["img_diag_request"] = updated
    state["vision_images_replaced"] = True
    return None


def _apply_human_scope_response(
    state: ImgDiagScopeGraphState,
    human: dict[str, Any],
) -> ImgDiagScopeGraphState:
    human = _normalize_human_scope_input(human if isinstance(human, dict) else {})
    action = str(human.get("action") or "confirm_scope")
    payload = human.get("payload") or {}
    if action == "abort":
        state["abort_requested"] = True
        state["abort_reason"] = str(payload.get("reason") or "user aborted scope confirm")
        return state

    _apply_hitl_image_urls_to_state(state, payload if isinstance(payload, dict) else {})

    pending_matched = bool(state.get("pending_matched_confirm"))
    pending_vision_ack = bool(state.get("pending_vision_user_ack"))
    affirmative_matched = pending_matched and is_matched_confirm_affirmative_response(action, payload)
    _apply_scope_correction_from_payload(
        state,
        payload if isinstance(payload, dict) else {},
        action=action,
    )
    pending_vision_ack = bool(state.get("pending_vision_user_ack"))

    if pending_vision_ack:
        payload_dict = payload if isinstance(payload, dict) else {}
        if _hitl_payload_only_replaced_images(payload_dict):
            apply_vision_rejection_scope_gate(state)
            if not is_scope_confirm_blocked_by_vision(
                state.get("vision_prefetch_data")
                if isinstance(state.get("vision_prefetch_data"), dict)
                else None,
                img_diag_request=state.get("img_diag_request")
                if isinstance(state.get("img_diag_request"), dict)
                else None,
                img_diag_subtype=str(state.get("img_diag_subtype") or "defect_ident"),
            ):
                state.pop("vision_confirm_blocked", None)
                if str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON:
                    state.pop("interrupt_reason", None)
        elif pending_vision_ack and is_matched_confirm_affirmative_response(action, payload):
            if state.pop("vision_prefetch_resume_refreshed", None):
                state.pop("vision_confirm_blocked", None)
            if _should_block_scope_confirm_by_vision_state(state):
                apply_vision_rejection_scope_gate(state)
            else:
                state.pop("pending_vision_user_ack", None)
                if str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON:
                    state.pop("interrupt_reason", None)
                state.pop("vision_confirm_blocked", None)

    if pending_matched:
        affirmative = affirmative_matched
        if _hitl_payload_only_replaced_images(payload if isinstance(payload, dict) else {}):
            apply_vision_rejection_scope_gate(state)
            if not is_scope_confirm_blocked_by_vision(
                state.get("vision_prefetch_data")
                if isinstance(state.get("vision_prefetch_data"), dict)
                else None,
                img_diag_request=state.get("img_diag_request")
                if isinstance(state.get("img_diag_request"), dict)
                else None,
                img_diag_subtype=str(state.get("img_diag_subtype") or "defect_ident"),
            ):
                sync_scope_hitl_after_vision_accepted(state)
        elif affirmative:
            if state.pop("vision_prefetch_resume_refreshed", None):
                sync_scope_hitl_after_vision_accepted(state)
            if _should_block_scope_confirm_by_vision_state(state):
                apply_vision_rejection_scope_gate(state)
            else:
                _finalize_confirmed_scope(state)
                if str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON:
                    state.pop("interrupt_reason", None)
                state.pop("vision_confirm_blocked", None)
    state["needs_db_retry"] = False
    state["validation_error"] = None
    return state


def make_img_diag_scope_nodes(
    *,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
) -> dict[str, Any]:
    client = llm_client or VLLMHttpClient()
    prompts = prompt_registry or PromptTemplateRegistry()

    async def scope_preflight_llm(state: ImgDiagScopeGraphState) -> ImgDiagScopeGraphState:
        state["scope_parse_attempts"] = int(state.get("scope_parse_attempts") or 0) + 1
        cumulative = (state.get("scope_cumulative_text") or state.get("query") or "").strip()
        state["scope_cumulative_text"] = cumulative
        excluded = frozenset(state.get("scope_field_exclusions") or ())
        draft = parse_img_diag_scope_draft(
            cumulative,
            llm_client=client,
            prompt_registry=prompts,
            scope_field_exclusions=excluded,
        )
        state["scope_draft"] = draft.to_dict()
        state["scope_confidence"] = draft.confidence
        state["missing_fields"] = missing_required_scope_fields(draft)
        trigger, reason = should_trigger_scope_hitl(draft)
        state["interrupt_reason"] = reason if trigger else ""
        if trigger:
            state["human_prompt"] = SCOPE_HITL_NOT_PARSED_PROMPT
            record_scope_hitl_context(
                state,
                reason=reason if trigger else "",
                prompt=SCOPE_HITL_NOT_PARSED_PROMPT,
            )
        return state

    async def scope_human_confirm(state: ImgDiagScopeGraphState) -> ImgDiagScopeGraphState:
        from langgraph.types import interrupt  # type: ignore[import-not-found]

        state["hitl_rounds"] = int(state.get("hitl_rounds") or 0) + 1
        payload = _build_interrupt_payload(state)
        human = interrupt(payload)
        if not isinstance(human, dict):
            human = {"action": "abort", "payload": {}}
        human = _normalize_human_scope_input(human)
        state.setdefault("human_interactions", []).append(
            {"request": payload, "response": human}
        )
        return _apply_human_scope_response(state, human)

    async def scope_db_validate(state: ImgDiagScopeGraphState) -> ImgDiagScopeGraphState:
        draft_dict = state.get("scope_draft") or {}
        scope = _scope_draft_payload(state)
        hitl_rounds = int(state.get("hitl_rounds") or 0)
        allow_auto_relax = scope_auto_relax_allowed(hitl_rounds=hitl_rounds)

        count, effective_scope, relaxed_fields, err = await validate_scope_with_relaxation(
            scope,
            allow_auto_relax=allow_auto_relax,
        )

        if count <= 0:
            state["validation_error"] = err or "scope_not_found_in_catalog"
            state["needs_db_retry"] = True
            state["human_prompt"] = SCOPE_HITL_DB_NOT_MATCHED_PROMPT
            state["interrupt_reason"] = "db_validate_zero_rows"
            state["scope_relaxed_fields"] = []
            record_scope_hitl_context(
                state,
                reason="db_validate_zero_rows",
                prompt=SCOPE_HITL_DB_NOT_MATCHED_PROMPT,
            )
            return state

        tm = parse_img_diag_scope_draft(state.get("scope_cumulative_text") or "").time_meta
        merged = apply_scope_patch(
            draft_from_scope_dict(draft_dict, time_meta=tm),
            effective_scope,
        )
        state["scope_draft"] = merged.to_dict()
        state["needs_db_retry"] = False
        state["validation_error"] = None
        if relaxed_fields:
            state["scope_relaxed_fields"] = relaxed_fields
            logger.info(
                "img_diag scope auto-relaxed fields=%s effective_scope=%s",
                relaxed_fields,
                effective_scope,
            )
        else:
            state["scope_relaxed_fields"] = []

        if relaxed_fields:
            state["confirmed_scope_intent"] = confirmed_scope_from_draft(merged)
            state["confirmed_scope_intent"]["scope_relaxed_fields"] = relaxed_fields
        else:
            state["confirmed_scope_intent"] = confirmed_scope_from_draft(merged)
        state["scope_intent_text"] = build_scope_intent_text(
            merged,
            scope_question=state.get("scope_cumulative_text") or "",
        )
        apply_vision_rejection_scope_gate(state)
        state["pending_matched_confirm"] = False
        if (
            state.get("confirmed_scope_intent")
            and state.get("scope_intent_text")
            and _needs_vision_user_ack_after_scope_db_match(state)
        ):
            state["pending_vision_user_ack"] = True
        else:
            state.pop("pending_vision_user_ack", None)
        return state

    return {
        "scope_preflight_llm": scope_preflight_llm,
        "scope_human_confirm": scope_human_confirm,
        "scope_db_validate": scope_db_validate,
    }


def _route_after_preflight(state: ImgDiagScopeGraphState) -> Literal["scope_human_confirm", "scope_db_validate"]:
    if state.get("abort_requested"):
        return "scope_db_validate"
    draft = _draft_from_state(state)
    if should_trigger_scope_hitl(draft)[0]:
        return "scope_human_confirm"
    return "scope_db_validate"


def _route_after_validate(state: ImgDiagScopeGraphState):
    if str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON:
        return "scope_human_confirm"
    if state.get("pending_vision_user_ack"):
        return "scope_human_confirm"
    if state.get("pending_matched_confirm"):
        return "scope_human_confirm"
    if state.get("confirmed_scope_intent") and state.get("scope_intent_text"):
        from langgraph.graph import END  # type: ignore[import-not-found]

        return END
    if state.get("abort_requested"):
        from langgraph.graph import END  # type: ignore[import-not-found]

        return END
    if int(state.get("hitl_rounds") or 0) >= _max_hitl_rounds():
        state["abort_requested"] = True
        state["abort_reason"] = "scope_hitl_max_rounds_exceeded"
        from langgraph.graph import END  # type: ignore[import-not-found]

        return END
    if state.get("needs_db_retry"):
        return "scope_human_confirm"
    from langgraph.graph import END  # type: ignore[import-not-found]

    return END


def _route_after_human_confirm(state: ImgDiagScopeGraphState):
    if (
        state.get("confirmed_scope_intent")
        and state.get("scope_intent_text")
        and not state.get("pending_vision_user_ack")
    ):
        from langgraph.graph import END  # type: ignore[import-not-found]

        return END
    if state.pop("scope_correction_pending_reparse", None):
        return "scope_preflight_llm"
    if _last_human_response_needs_scope_reparse(state):
        return "scope_preflight_llm"
    if _state_needs_scope_hitl_interrupt(state):
        from langgraph.graph import END  # type: ignore[import-not-found]

        return END
    return "scope_preflight_llm"


def build_img_diag_scope_graph(
    *,
    llm_client: VLLMHttpClient | None = None,
    prompt_registry: PromptTemplateRegistry | None = None,
) -> tuple[Any | None, Any | None]:
    try:
        from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("langgraph not available; img_diag scope HITL disabled")
        return None, None

    nodes = make_img_diag_scope_nodes(
        llm_client=llm_client,
        prompt_registry=prompt_registry,
    )
    g = StateGraph(ImgDiagScopeGraphState)
    g.add_node("scope_preflight_llm", nodes["scope_preflight_llm"])
    g.add_node("scope_human_confirm", nodes["scope_human_confirm"])
    g.add_node("scope_db_validate", nodes["scope_db_validate"])
    g.set_entry_point("scope_preflight_llm")
    g.add_conditional_edges("scope_preflight_llm", _route_after_preflight)
    g.add_conditional_edges("scope_human_confirm", _route_after_human_confirm)
    g.add_conditional_edges("scope_db_validate", _route_after_validate)
    checkpointer = build_img_diag_checkpointer()
    if checkpointer is None:
        return None, None
    return g.compile(checkpointer=checkpointer), checkpointer


class ImgDiagScopeHitlRunner:
    """看图诊断 scope 人机协同子图运行器。"""

    def __init__(
        self,
        *,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
    ) -> None:
        self._llm = llm_client or VLLMHttpClient()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._graph = None
        self._checkpointer = None
        if self._use_langgraph() and self._scope_hitl_enabled():
            self._graph, self._checkpointer = build_img_diag_scope_graph(
                llm_client=self._llm,
                prompt_registry=self._prompts,
            )

    @staticmethod
    def _use_langgraph() -> bool:
        return bool(getattr(_cfg(), "img_diag_use_langgraph", True))

    @staticmethod
    def _scope_hitl_enabled() -> bool:
        return bool(getattr(_cfg(), "img_diag_scope_hitl_enabled", True))

    def available(self) -> bool:
        return self._graph is not None and self._checkpointer is not None

    def _build_initial_state(
        self,
        *,
        request_id: str,
        img_diag_request: dict[str, Any],
        orchestrator_path: str = "scope_first",
        vision_prefetch: dict[str, Any] | None = None,
        vision_prefetch_ms: int = 0,
        vision_prefetch_status: str = "",
    ) -> ImgDiagScopeGraphState:
        req = img_diag_request
        opts = req.get("options") or {}
        if isinstance(opts, dict) and hasattr(opts, "model_dump"):
            opts = opts.model_dump(mode="json")
        subtype = req.get("img_diag_subtype", "defect_ident")
        at = (
            "img_diag_leakage_burst"
            if subtype == "leakage_burst"
            else "img_diag_defect_ident"
        )
        query = (req.get("query") or "").strip()
        state: ImgDiagScopeGraphState = {
            "request_id": request_id,
            "user_id": req.get("user_id", ""),
            "session_id": req.get("session_id", ""),
            "analysis_type": at,
            "img_diag_subtype": subtype,
            "query": query,
            "options": opts if isinstance(opts, dict) else {},
            "img_diag_request": req,
            "scope_cumulative_text": query,
            "scope_parse_attempts": 0,
            "hitl_rounds": 0,
            "human_interactions": [],
            "orchestrator_path": orchestrator_path or "scope_first",
        }
        if not query and normalize_image_url_list(req.get("image_urls")):
            state["initial_query_empty"] = True
        if isinstance(vision_prefetch, dict) and vision_prefetch:
            state["vision_prefetch_data"] = vision_prefetch
            state["vision_prefetch_ms"] = int(vision_prefetch_ms or 0)
            state["vision_prefetch_status"] = str(vision_prefetch_status or "")
        return state

    async def _persist_scope_human_confirm_interrupt_gate(
        self,
        config: dict[str, Any],
        *,
        final_state: dict[str, Any],
        interrupt_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """interrupt 后把 hitl/vision 展示门禁写回 checkpoint，供后续 resume 正确判定。"""
        assert self._graph is not None
        snap = await self._graph.aget_state(config)
        state = dict(snap.values or {}) if snap and snap.values else dict(final_state)
        state.update(final_state)
        _sync_scope_human_confirm_hitl_gate_flags(state, interrupt_payload=interrupt_payload)
        as_node = _scope_resume_checkpoint_as_node(snap)
        await self._graph.aupdate_state(config, state, as_node=as_node)
        return state

    async def _yield_updates(
        self,
        *,
        input_state: Any,
        config: dict[str, Any],
        expect_resume: bool = False,
    ):
        assert self._graph is not None
        saw_state_update = False
        async for chunk in self._graph.astream(input_state, config, stream_mode="updates"):
            if not isinstance(chunk, dict):
                continue
            if "__interrupt__" in chunk:
                for intr in chunk["__interrupt__"]:
                    payload = intr.value if hasattr(intr, "value") else intr
                    if isinstance(payload, dict):
                        yield {"_interrupt_payload": payload}
                continue
            for _node, update in chunk.items():
                if isinstance(update, dict):
                    saw_state_update = True
                    yield {"_state_update": update}

        snapshot = await self._graph.aget_state(config)
        if snapshot is not None and getattr(snapshot, "interrupts", None):
            if expect_resume and not saw_state_update:
                logger.warning(
                    "img_diag scope resume: Command(resume) produced no node updates; "
                    "falling back to snapshot interrupt payload"
                )
            for intr in snapshot.interrupts:
                val = intr.value if hasattr(intr, "value") else intr
                if isinstance(val, dict):
                    yield {"_interrupt_payload": val}

    async def _maybe_force_scope_reparse_after_resume(
        self,
        *,
        config: dict[str, Any],
        state: dict[str, Any],
        payload: dict[str, Any],
        action: str,
        parse_attempts_before: int,
    ) -> dict[str, Any]:
        """Command(resume) 未消化本轮台账校正时，强制执行解析+库表校验。"""
        if not _resume_payload_needs_scope_reparse(payload, action=action, state=state):
            return state
        _apply_scope_correction_from_payload(state, payload, action=action)
        if not _scope_correction_needs_reparse(state):
            return state
        logger.info(
            "img_diag scope resume: forcing scope reparse after correction payload "
            "request_id=%s parse_attempts_before=%s correction_epoch=%s parsed_epoch=%s",
            state.get("request_id"),
            parse_attempts_before,
            state.get("scope_correction_epoch"),
            state.get("scope_correction_parsed_epoch"),
        )
        state = await _run_scope_reparse_pipeline(
            state,
            llm_client=self._llm,
            prompt_registry=self._prompts,
        )
        assert self._graph is not None
        snap_now = await self._graph.aget_state(config)
        as_node = _scope_resume_checkpoint_as_node(snap_now)
        await self._graph.aupdate_state(config, state, as_node=as_node)
        return state

    async def _reparse_scope_after_prep_if_needed(
        self,
        *,
        config: dict[str, Any],
        payload: dict[str, Any],
        action: str,
    ) -> bool:
        """prep 已合并校正但 checkpoint 尚未消化时，在 Command(resume) 前强制解析+校验。"""
        assert self._graph is not None
        snap = await self._graph.aget_state(config)
        if not snap or not snap.values:
            return False
        state = dict(snap.values)
        if not _scope_correction_needs_reparse(state):
            return False
        logger.info(
            "img_diag scope resume: prep-time scope reparse request_id=%s "
            "correction_epoch=%s parsed_epoch=%s pending_supplement=%s",
            state.get("request_id"),
            state.get("scope_correction_epoch"),
            state.get("scope_correction_parsed_epoch"),
            bool(state.get("scope_pending_reparse_supplement")),
        )
        state = await _run_scope_reparse_pipeline(
            state,
            llm_client=self._llm,
            prompt_registry=self._prompts,
        )
        as_node = _scope_resume_checkpoint_as_node(snap)
        await self._graph.aupdate_state(config, state, as_node=as_node)
        return True

    async def _hydrate_scope_resume_state_from_session(
        self,
        state: dict[str, Any],
        *,
        session: Any,
        user_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        out = dict(state)
        if not out.get("vision_prefetch_data") and session.vision_prefetch:
            orch = str(out.get("orchestrator_path") or session.orchestrator_path or "scope_first")
            req_probe = dict(out.get("img_diag_request") or session.img_diag_request or {})
            if not (orch == "vision_first" and normalize_image_url_list(req_probe.get("image_urls"))):
                out["vision_prefetch_data"] = session.vision_prefetch
                out["vision_prefetch_ms"] = session.vision_prefetch_ms
                out["vision_prefetch_status"] = session.vision_prefetch_status
        out.setdefault("orchestrator_path", session.orchestrator_path)
        out.setdefault("user_id", user_id)
        out.setdefault("session_id", session_id)
        out.setdefault("analysis_type", session.analysis_type)
        out.setdefault("img_diag_subtype", session.img_diag_subtype)
        return out

    async def _try_finalize_after_prep_reparse(
        self,
        *,
        config: dict[str, Any],
        session: Any,
        user_id: str,
        session_id: str,
        action: str,
        payload: dict[str, Any],
        resume_token: str,
        vision_refresh: VisionRefreshFn | None,
        resume_prep_persisted: bool,
    ) -> dict[str, Any] | None:
        """
        prep 重解析已完成时直接收口（confirmed 或 interrupt），
        避免 Command(resume) 用旧 graph 覆盖 checkpoint。
        台账校验+视觉均通过时沿用 scope_db_validate 自动放行，不再强制回到人机确认。
        """
        assert self._graph is not None
        snap = await self._graph.aget_state(config)
        if not snap or not snap.values:
            return None
        state = dict(snap.values)
        if _scope_correction_needs_reparse(state):
            return None
        auto_confirmed = bool(
            state.get("confirmed_scope_intent")
            and state.get("scope_intent_text")
            and not state.get("pending_vision_user_ack")
        )
        needs_interrupt = bool(
            state.get("pending_matched_confirm")
            or state.get("pending_vision_user_ack")
            or _state_needs_scope_hitl_interrupt(state)
        )
        if not auto_confirmed and not needs_interrupt:
            return None

        human_input = _normalize_human_scope_input({"action": action, "payload": payload})
        state["hitl_rounds"] = int(state.get("hitl_rounds") or 0) + 1
        as_node = _scope_resume_checkpoint_as_node(snap)

        state_out = await self._hydrate_scope_resume_state_from_session(
            state,
            session=session,
            user_id=user_id,
            session_id=session_id,
        )

        if auto_confirmed:
            updated_request = await _finalize_state_after_hitl_resume(
                state_out,
                session=session,
                vision_refresh=vision_refresh,
                for_interrupt=False,
            )
            if _should_block_scope_confirm_by_vision_state(state_out):
                apply_vision_rejection_scope_gate(state_out)
                await self._graph.aupdate_state(config, state_out, as_node=as_node)
                updated_request = await _finalize_state_after_hitl_resume(
                    state_out,
                    session=session,
                    vision_refresh=vision_refresh,
                    for_interrupt=True,
                )
                intr = _build_interrupt_payload(state_out)
                state_out.setdefault("human_interactions", []).append(
                    {"request": intr, "response": human_input}
                )
                await self._graph.aupdate_state(config, state_out, as_node=as_node)
                _log_scope_resume_diagnostics(
                    request_id=session.request_id,
                    action=action,
                    payload=payload,
                    state=state_out,
                    status="interrupt",
                    graph_ran=False,
                    still_interrupted=True,
                    resume_prep_persisted=resume_prep_persisted,
                )
                new_token = create_img_diag_resume_token(
                    **_resume_session_kwargs(
                        state_out,
                        thread_id=session.thread_id,
                        request_id=session.request_id,
                        interrupt_payload=intr,
                        img_diag_request=updated_request,
                    )
                )
                delete_img_diag_resume_session(resume_token)
                return {
                    "status": "interrupt",
                    "request_id": session.request_id,
                    "resume_token": new_token,
                    "interrupt_payload": intr,
                    "img_diag_request": updated_request,
                    "orchestrator_path": session.orchestrator_path,
                    "vision_prefetch": state_out.get("vision_prefetch_data"),
                    "vision_prefetch_ms": int(state_out.get("vision_prefetch_ms") or 0),
                    "vision_prefetch_status": str(state_out.get("vision_prefetch_status") or ""),
                }

            _log_scope_resume_diagnostics(
                request_id=session.request_id,
                action=action,
                payload=payload,
                state=state_out,
                status="confirmed",
                graph_ran=False,
                still_interrupted=False,
                resume_prep_persisted=resume_prep_persisted,
            )
            delete_img_diag_resume_session(resume_token)
            return {
                "status": "confirmed",
                "request_id": session.request_id,
                "confirmed_scope_intent": state_out["confirmed_scope_intent"],
                "scope_intent_text": state_out["scope_intent_text"],
                "img_diag_request": updated_request,
                "orchestrator_path": session.orchestrator_path,
                "vision_prefetch": state_out.get("vision_prefetch_data"),
                "vision_prefetch_ms": int(state_out.get("vision_prefetch_ms") or 0),
                "vision_prefetch_status": str(state_out.get("vision_prefetch_status") or ""),
                **_scope_hitl_result_extras(state_out),
            }

        updated_request = await _finalize_state_after_hitl_resume(
            state_out,
            session=session,
            vision_refresh=vision_refresh,
            for_interrupt=True,
        )
        intr = _build_interrupt_payload(state_out)
        state_out.setdefault("human_interactions", []).append(
            {"request": intr, "response": human_input}
        )
        await self._graph.aupdate_state(config, state_out, as_node=as_node)
        _log_scope_resume_diagnostics(
            request_id=session.request_id,
            action=action,
            payload=payload,
            state=state_out,
            status="interrupt",
            graph_ran=False,
            still_interrupted=True,
            resume_prep_persisted=resume_prep_persisted,
        )
        new_token = create_img_diag_resume_token(
            **_resume_session_kwargs(
                state_out,
                thread_id=session.thread_id,
                request_id=session.request_id,
                interrupt_payload=intr,
                img_diag_request=updated_request,
            )
        )
        delete_img_diag_resume_session(resume_token)
        return {
            "status": "interrupt",
            "request_id": session.request_id,
            "resume_token": new_token,
            "interrupt_payload": intr,
            "img_diag_request": updated_request,
            "orchestrator_path": session.orchestrator_path,
            "vision_prefetch": state_out.get("vision_prefetch_data"),
            "vision_prefetch_ms": int(state_out.get("vision_prefetch_ms") or 0),
            "vision_prefetch_status": str(state_out.get("vision_prefetch_status") or ""),
        }

    async def run_until_scope_confirmed_or_interrupt(
        self,
        img_diag_request: dict[str, Any],
        *,
        request_id: str | None = None,
        orchestrator_path: str = "scope_first",
        vision_prefetch: dict[str, Any] | None = None,
        vision_prefetch_ms: int = 0,
        vision_prefetch_status: str = "",
    ) -> dict[str, Any]:
        """
        返回 dict:
        - status: confirmed | interrupt | skipped | error
        - confirmed_scope_intent, scope_intent_text (if confirmed)
        - interrupt_payload, resume_token (if interrupt)
        """
        if not self._scope_hitl_enabled():
            return {"status": "skipped"}
        if not self.available():
            reason = "langgraph_or_checkpoint_unavailable"
            logger.warning(
                "img_diag scope HITL skipped: %s (check ANALYSIS_IMG_DIAG_USE_LANGGRAPH / "
                "SCOPE_HITL_ENABLED / checkpoint backend)",
                reason,
            )
            return {"status": "skipped", "reason": reason}

        rid = request_id or f"anl_{uuid.uuid4().hex[:12]}"
        initial = self._build_initial_state(
            request_id=rid,
            img_diag_request=img_diag_request,
            orchestrator_path=orchestrator_path,
            vision_prefetch=vision_prefetch,
            vision_prefetch_ms=vision_prefetch_ms,
            vision_prefetch_status=vision_prefetch_status,
        )
        config = img_diag_graph_configurable(rid)
        final_state: dict[str, Any] = dict(initial)

        async for ev in self._yield_updates(input_state=initial, config=config):
            if "_state_update" in ev:
                final_state.update(ev["_state_update"])
            if "_interrupt_payload" in ev:
                intr = ev["_interrupt_payload"]
                gate_state = await self._persist_scope_human_confirm_interrupt_gate(
                    config,
                    final_state=final_state,
                    interrupt_payload=intr,
                )
                final_state.update(gate_state)
                stored_urls = (
                    img_diag_request.get("image_urls")
                    if isinstance(img_diag_request, dict)
                    else []
                )
                url_count = len(
                    [u for u in (stored_urls or []) if isinstance(u, str) and u.strip()]
                )
                token = create_img_diag_resume_token(
                    **_resume_session_kwargs(
                        final_state,
                        thread_id=rid,
                        request_id=rid,
                        interrupt_payload=intr,
                        img_diag_request=img_diag_request,
                    )
                )
                logger.info(
                    "img_diag scope interrupt store resume session request_id=%s "
                    "subtype=%s image_urls url_count=%s raw_list_len=%s",
                    rid,
                    initial.get("img_diag_subtype"),
                    url_count,
                    len(stored_urls or []),
                )
                return {
                    "status": "interrupt",
                    "request_id": rid,
                    "resume_token": token,
                    "interrupt_payload": intr,
                }

        snap = await self._graph.aget_state(config)
        if snap and snap.values:
            final_state.update(dict(snap.values))

        if final_state.get("abort_requested"):
            return {
                "status": "error",
                "message": final_state.get("abort_reason") or "scope confirm aborted",
            }
        if final_state.get("confirmed_scope_intent") and final_state.get("scope_intent_text"):
            if apply_vision_rejection_scope_gate(final_state):
                return _scope_interrupt_from_state(
                    final_state,
                    thread_id=rid,
                    request_id=rid,
                    img_diag_request=img_diag_request,
                )
            if final_state.get("pending_vision_user_ack"):
                return _scope_interrupt_from_state(
                    final_state,
                    thread_id=rid,
                    request_id=rid,
                    img_diag_request=img_diag_request,
                )
            return {
                "status": "confirmed",
                "request_id": rid,
                "confirmed_scope_intent": final_state["confirmed_scope_intent"],
                "scope_intent_text": final_state["scope_intent_text"],
                **_scope_hitl_result_extras(final_state),
            }
        if _state_needs_scope_hitl_interrupt(final_state):
            return _scope_interrupt_from_state(
                final_state,
                thread_id=rid,
                request_id=rid,
                img_diag_request=img_diag_request,
            )
        return {"status": "error", "message": "scope confirm incomplete"}

    async def resume_until_confirmed_or_interrupt(
        self,
        *,
        resume_token: str,
        user_id: str,
        session_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        vision_refresh: VisionRefreshFn | None = None,
    ) -> dict[str, Any]:
        session = get_img_diag_resume_session(resume_token)
        if session is None:
            return {"status": "error", "message": "invalid or expired resume_token"}
        if session.user_id != user_id or session.session_id != session_id:
            return {"status": "error", "message": "resume_token session mismatch"}
        if not self.available():
            return {"status": "error", "message": "checkpoint not enabled"}

        payload = dict(payload or {})
        human_input = _normalize_human_scope_input({"action": action, "payload": payload})
        action = str(human_input.get("action") or action)
        payload = dict(human_input.get("payload") or {})
        if "image_urls" in payload:
            req_probe = dict(session.img_diag_request or {})
            updated_probe, changed_probe = merge_hitl_image_urls_into_request(req_probe, payload)
            if changed_probe:
                subtype = str(
                    updated_probe.get("img_diag_subtype")
                    or session.img_diag_subtype
                    or "defect_ident"
                )
                img_err = validate_hitl_image_urls_for_subtype(
                    img_diag_subtype=subtype,
                    image_urls=list(updated_probe.get("image_urls") or []),
                )
                if img_err:
                    return {"status": "error", "message": img_err}

        try:
            from langgraph.types import Command  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "message": "langgraph Command unavailable"}

        config = img_diag_graph_configurable(session.thread_id)
        snap_before_prep = await self._graph.aget_state(config)
        prep_err = await _prepare_scope_resume_state(
            self._graph,
            thread_id=session.thread_id,
            session=session,
            payload=payload,
            action=action,
            vision_refresh=vision_refresh,
        )
        if prep_err:
            return {"status": "error", "message": prep_err}
        snap_after_prep = await self._graph.aget_state(config)
        prep_state = dict(snap_after_prep.values or {}) if snap_after_prep and snap_after_prep.values else {}
        prep_reparsed = await self._reparse_scope_after_prep_if_needed(
            config=config,
            payload=payload,
            action=action,
        )
        if prep_reparsed:
            snap_after_prep = await self._graph.aget_state(config)
            prep_state = dict(snap_after_prep.values or {}) if snap_after_prep and snap_after_prep.values else {}
        parse_attempts_before = int(prep_state.get("scope_parse_attempts") or 0)
        resume_prep_persisted = snap_before_prep is not None and snap_after_prep is not None and (
            dict(snap_before_prep.values or {}) != dict(snap_after_prep.values or {})
        )

        early = await _try_resolve_vision_user_ack_after_prep(
            self._graph,
            config,
            session,
            action=action,
            payload=payload,
            vision_refresh=vision_refresh,
        )
        if early is not None:
            delete_img_diag_resume_session(resume_token)
            return early

        early = await _try_resolve_matched_confirm_after_prep(
            self._graph,
            config,
            session,
            action=action,
            payload=payload,
            vision_refresh=vision_refresh,
        )
        if early is not None:
            delete_img_diag_resume_session(resume_token)
            return early

        if prep_reparsed:
            prep_result = await self._try_finalize_after_prep_reparse(
                config=config,
                session=session,
                user_id=user_id,
                session_id=session_id,
                action=action,
                payload=payload,
                resume_token=resume_token,
                vision_refresh=vision_refresh,
                resume_prep_persisted=resume_prep_persisted,
            )
            if prep_result is not None:
                return prep_result

        human_input = _normalize_human_scope_input({"action": action, "payload": payload})
        final_state: dict[str, Any] = {}
        graph_ran = False

        async for ev in self._yield_updates(
            input_state=Command(resume=human_input),
            config=config,
            expect_resume=True,
        ):
            if "_state_update" in ev:
                graph_ran = True
                final_state.update(ev["_state_update"])
            if "_interrupt_payload" in ev:
                snap_early = await self._graph.aget_state(config)
                checkpoint = dict(snap_early.values) if snap_early and snap_early.values else {}
                state_for_token = _merge_scope_resume_interrupt_state(checkpoint, final_state)
                state_for_token = await self._maybe_force_scope_reparse_after_resume(
                    config=config,
                    state=state_for_token,
                    payload=payload,
                    action=action,
                    parse_attempts_before=parse_attempts_before,
                )
                if not state_for_token.get("vision_prefetch_data") and session.vision_prefetch:
                    orch = str(
                        state_for_token.get("orchestrator_path") or session.orchestrator_path or "scope_first"
                    )
                    req_probe = dict(state_for_token.get("img_diag_request") or session.img_diag_request or {})
                    if not (orch == "vision_first" and normalize_image_url_list(req_probe.get("image_urls"))):
                        state_for_token["vision_prefetch_data"] = session.vision_prefetch
                        state_for_token["vision_prefetch_ms"] = session.vision_prefetch_ms
                        state_for_token["vision_prefetch_status"] = session.vision_prefetch_status
                state_for_token.setdefault("orchestrator_path", session.orchestrator_path)
                state_for_token.setdefault("user_id", user_id)
                state_for_token.setdefault("session_id", session_id)
                state_for_token.setdefault("analysis_type", session.analysis_type)
                state_for_token.setdefault("img_diag_subtype", session.img_diag_subtype)
                updated_request = await _finalize_state_after_hitl_resume(
                    state_for_token,
                    session=session,
                    vision_refresh=vision_refresh,
                    for_interrupt=True,
                )
                # 必须先按当前 state 重建 interrupt，再用该 payload 同步 delivered；
                # 若先用图内旧 interrupt 标记 delivered，再重建会把「首次通过态」图像分析丢掉。
                intr = _build_interrupt_payload(state_for_token)
                _sync_scope_human_confirm_hitl_gate_flags(
                    state_for_token,
                    interrupt_payload=intr,
                )
                await self._graph.aupdate_state(
                    config,
                    state_for_token,
                    as_node=_scope_resume_checkpoint_as_node(snap_early),
                )
                _log_scope_resume_diagnostics(
                    request_id=session.request_id,
                    action=action,
                    payload=payload,
                    state=state_for_token,
                    status="interrupt",
                    graph_ran=graph_ran,
                    still_interrupted=bool(getattr(snap_early, "interrupts", None)),
                    resume_prep_persisted=resume_prep_persisted,
                )
                new_token = create_img_diag_resume_token(
                    **_resume_session_kwargs(
                        state_for_token,
                        thread_id=session.thread_id,
                        request_id=session.request_id,
                        interrupt_payload=intr,
                        img_diag_request=updated_request,
                    )
                )
                delete_img_diag_resume_session(resume_token)
                return {
                    "status": "interrupt",
                    "request_id": session.request_id,
                    "resume_token": new_token,
                    "interrupt_payload": intr,
                    "img_diag_request": updated_request,
                    "orchestrator_path": session.orchestrator_path,
                    "vision_prefetch": state_for_token.get("vision_prefetch_data"),
                    "vision_prefetch_ms": int(state_for_token.get("vision_prefetch_ms") or 0),
                    "vision_prefetch_status": str(state_for_token.get("vision_prefetch_status") or ""),
                }

        delete_img_diag_resume_session(resume_token)
        snap = await self._graph.aget_state(config)
        if snap and snap.values:
            final_state = _merge_scope_resume_interrupt_state(dict(snap.values), final_state)
        final_state = await self._maybe_force_scope_reparse_after_resume(
            config=config,
            state=final_state,
            payload=payload,
            action=action,
            parse_attempts_before=parse_attempts_before,
        )

        if final_state.get("abort_requested"):
            return {
                "status": "error",
                "message": final_state.get("abort_reason") or "scope confirm aborted",
            }
        if final_state.get("confirmed_scope_intent") and final_state.get("scope_intent_text"):
            state_confirmed: dict[str, Any] = dict(final_state)
            state_confirmed.setdefault("orchestrator_path", session.orchestrator_path)
            updated_request = await _finalize_state_after_hitl_resume(
                state_confirmed,
                session=session,
                vision_refresh=vision_refresh,
                for_interrupt=False,
            )
            if _should_block_scope_confirm_by_vision_state(state_confirmed):
                apply_vision_rejection_scope_gate(state_confirmed)
                return _scope_interrupt_from_state(
                    state_confirmed,
                    thread_id=session.thread_id,
                    request_id=session.request_id,
                    img_diag_request=updated_request if isinstance(updated_request, dict) else session.img_diag_request or {},
                )
            stored_urls = updated_request.get("image_urls") if isinstance(updated_request, dict) else []
            url_count = len(normalize_image_url_list(stored_urls))
            logger.info(
                "img_diag scope resume confirmed request_id=%s action=%s "
                "session_image_urls url_count=%s vision_replaced=%s",
                session.request_id,
                action,
                url_count,
                bool(state_confirmed.get("vision_images_replaced")),
            )
            _log_scope_resume_diagnostics(
                request_id=session.request_id,
                action=action,
                payload=payload,
                state=state_confirmed,
                status="confirmed",
                graph_ran=graph_ran,
                resume_prep_persisted=resume_prep_persisted,
            )
            return {
                "status": "confirmed",
                "request_id": session.request_id,
                "confirmed_scope_intent": final_state["confirmed_scope_intent"],
                "scope_intent_text": final_state["scope_intent_text"],
                "img_diag_request": updated_request,
                "orchestrator_path": session.orchestrator_path,
                "vision_prefetch": state_confirmed.get("vision_prefetch_data"),
                "vision_prefetch_ms": int(state_confirmed.get("vision_prefetch_ms") or 0),
                "vision_prefetch_status": str(state_confirmed.get("vision_prefetch_status") or ""),
                **_scope_hitl_result_extras(state_confirmed),
            }
        if _state_needs_scope_hitl_interrupt(final_state):
            state_intr: dict[str, Any] = dict(final_state)
            state_intr.setdefault("orchestrator_path", session.orchestrator_path)
            intr_request = dict(state_intr.get("img_diag_request") or session.img_diag_request or {})
            return _scope_interrupt_from_state(
                state_intr,
                thread_id=session.thread_id,
                request_id=session.request_id,
                img_diag_request=intr_request,
            )
        return {"status": "error", "message": "scope confirm incomplete after resume"}
