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
    hitl_rounds = int(state.get("hitl_rounds") or 0)
    vision_data = state.get("vision_prefetch_data")
    images_replaced = bool(state.get("vision_images_replaced"))
    include_vision = (
        isinstance(vision_data, dict)
        and bool(vision_data)
        and (
            images_replaced
            or str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON
            or (
                orchestrator_path == "vision_first"
                and hitl_rounds <= 1
            )
        )
    )
    payload["include_vision_preview"] = include_vision
    if include_vision:
        subtype = str(state.get("img_diag_subtype") or "defect_ident")
        payload["vision_findings_display"] = build_vision_findings_display(
            vision_data,
            img_diag_subtype=subtype,
        )
    payload["confirm_reply_example"] = build_scope_hitl_confirm_reply_example(payload)


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
        vision_data, ms, status = await vision_refresh(updated_request)
        state["vision_prefetch_data"] = vision_data
        state["vision_prefetch_ms"] = int(ms or 0)
        state["vision_prefetch_status"] = str(status or "")
        state["vision_images_replaced"] = False
        state.pop("vision_prefetch_resume_refreshed", None)
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


async def _prepare_scope_resume_state(
    graph: Any,
    *,
    thread_id: str,
    session: Any,
    payload: dict[str, Any],
    vision_refresh: VisionRefreshFn | None,
) -> str | None:
    """resume 前合并换图 URL，并在 URL 变化时重跑视觉写回 checkpoint。"""
    config = img_diag_graph_configurable(thread_id)
    snap = await graph.aget_state(config)
    if not snap or not snap.values:
        return None
    state = dict(snap.values)
    payload_dict = payload if isinstance(payload, dict) else {}
    img_err = _apply_hitl_image_urls_to_state(state, payload_dict)
    if img_err:
        return img_err
    req = dict(state.get("img_diag_request") or session.img_diag_request or {})
    state["img_diag_request"] = req
    payload_urls = normalize_image_url_list(payload_dict.get("image_urls"))
    should_refresh_vision = vision_refresh is not None and (
        bool(state.get("vision_images_replaced")) or bool(payload_urls)
    )
    if should_refresh_vision:
        vision_data, ms, status = await vision_refresh(req)
        state["vision_prefetch_data"] = vision_data
        state["vision_prefetch_ms"] = int(ms or 0)
        state["vision_prefetch_status"] = str(status or "")
        state["vision_prefetch_resume_refreshed"] = True
        state["vision_images_replaced"] = False
        sync_scope_hitl_after_vision_accepted(state)
    await graph.aupdate_state(config, state)
    return None


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
    sync_scope_hitl_after_vision_accepted(state)


def _build_interrupt_payload(state: ImgDiagScopeGraphState) -> dict[str, Any]:
    _apply_vision_gate_or_restore_scope_hitl(state)
    scope_draft = _scope_draft_payload(state)
    missing = state.get("missing_fields") or []
    relaxed = state.get("scope_relaxed_fields") or []
    vision_blocked = bool(
        state.get("vision_confirm_blocked")
        or str(state.get("interrupt_reason") or "") == VISION_REJECT_INTERRUPT_REASON
    )
    payload: dict[str, Any] = {
        "prompt": resolve_scope_hitl_display_prompt(state=state),
        "scope_draft": scope_draft,
        "scope_draft_display": scope_draft_to_display(scope_draft),
        "missing_fields": [scope_field_label(f) for f in missing],
        "validation_error": state.get("validation_error"),
        "suggested_actions": state.get("human_suggested_actions")
        or ["confirm_scope", "edit_scope", "abort"],
        "request_id": state.get("request_id"),
        "interrupt_reason": state.get("interrupt_reason"),
        "pending_matched_confirm": bool(state.get("pending_matched_confirm")),
        "vision_confirm_blocked": vision_blocked,
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


def _state_needs_scope_hitl_interrupt(state: dict[str, Any]) -> bool:
    """图已结束但尚未 confirmed，仍须下一次人机协同（视觉/台账待确认）。"""
    if state.get("confirmed_scope_intent") and state.get("scope_intent_text"):
        return False
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
    action = str(human.get("action") or "confirm_scope")
    payload = human.get("payload") or {}
    if action == "abort":
        state["abort_requested"] = True
        state["abort_reason"] = str(payload.get("reason") or "user aborted scope confirm")
        return state

    _apply_hitl_image_urls_to_state(state, payload if isinstance(payload, dict) else {})

    supplement = str(payload.get("user_supplement") or "").strip()
    patch = payload.get("scope_patch")
    pending_matched = bool(state.get("pending_matched_confirm"))
    affirmative = pending_matched and is_matched_confirm_affirmative_response(action, payload)

    if not affirmative:
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
        if supplement:
            cumulative = (state.get("scope_cumulative_text") or state.get("query") or "").strip()
            state["scope_cumulative_text"] = f"{cumulative}\n{supplement}".strip()
        if isinstance(patch, dict) and state.get("scope_draft"):
            dd = state["scope_draft"]
            tm = parse_img_diag_scope_draft(state.get("scope_cumulative_text") or "").time_meta
            draft = draft_from_scope_dict(dd, time_meta=tm)
            draft = apply_scope_patch(draft, patch)
            excluded = frozenset(state.get("scope_field_exclusions") or ())
            state["scope_draft"] = apply_scope_field_exclusions_to_draft(draft, excluded).to_dict()
        elif state.get("scope_draft") and state.get("scope_field_exclusions"):
            dd = state["scope_draft"]
            tm = parse_img_diag_scope_draft(state.get("scope_cumulative_text") or "").time_meta
            draft = draft_from_scope_dict(dd, time_meta=tm)
            excluded = frozenset(state.get("scope_field_exclusions") or ())
            state["scope_draft"] = apply_scope_field_exclusions_to_draft(draft, excluded).to_dict()

    if pending_matched:
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
        else:
            state["pending_matched_confirm"] = False
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

        if _scope_matched_confirm_enabled() and hitl_rounds == 0:
            state["pending_matched_confirm"] = True
            state["human_prompt"] = SCOPE_HITL_DB_MATCHED_PROMPT
            state["interrupt_reason"] = "db_validate_matched"
            state["missing_fields"] = []
            record_scope_hitl_context(
                state,
                reason="db_validate_matched",
                prompt=SCOPE_HITL_DB_MATCHED_PROMPT,
            )
            apply_vision_rejection_scope_gate(state)
            return state

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
    if state.get("confirmed_scope_intent") and state.get("scope_intent_text"):
        from langgraph.graph import END  # type: ignore[import-not-found]

        return END
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
        if isinstance(vision_prefetch, dict) and vision_prefetch:
            state["vision_prefetch_data"] = vision_prefetch
            state["vision_prefetch_ms"] = int(vision_prefetch_ms or 0)
            state["vision_prefetch_status"] = str(vision_prefetch_status or "")
        return state

    async def _yield_updates(
        self,
        *,
        input_state: Any,
        config: dict[str, Any],
    ):
        assert self._graph is not None
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
                    yield {"_state_update": update}

        snapshot = await self._graph.aget_state(config)
        if snapshot is not None and getattr(snapshot, "interrupts", None):
            for intr in snapshot.interrupts:
                val = intr.value if hasattr(intr, "value") else intr
                if isinstance(val, dict):
                    yield {"_interrupt_payload": val}

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
            return {
                "status": "confirmed",
                "request_id": rid,
                "confirmed_scope_intent": final_state["confirmed_scope_intent"],
                "scope_intent_text": final_state["scope_intent_text"],
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

        payload = payload or {}
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
        prep_err = await _prepare_scope_resume_state(
            self._graph,
            thread_id=session.thread_id,
            session=session,
            payload=payload,
            vision_refresh=vision_refresh,
        )
        if prep_err:
            return {"status": "error", "message": prep_err}

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

        human_input = {"action": action, "payload": payload}
        final_state: dict[str, Any] = {}

        async for ev in self._yield_updates(
            input_state=Command(resume=human_input),
            config=config,
        ):
            if "_state_update" in ev:
                final_state.update(ev["_state_update"])
            if "_interrupt_payload" in ev:
                snap_early = await self._graph.aget_state(config)
                state_for_token: dict[str, Any] = (
                    dict(snap_early.values) if snap_early and snap_early.values else {}
                )
                state_for_token.update(final_state)
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
                intr = _build_interrupt_payload(state_for_token)
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
            final_state.update(dict(snap.values))

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
