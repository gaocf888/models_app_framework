"""看图诊断 scope HITL LangGraph 状态与编排。"""

from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.graphs.img_diag_checkpoint import (
    build_img_diag_checkpointer,
    img_diag_graph_configurable,
)
from app.llm.graphs.img_diag_scope_display import (
    SCOPE_HITL_DB_NOT_MATCHED_PROMPT,
    format_missing_fields_cn,
    normalize_scope_patch_keys,
    scope_draft_to_display,
    scope_field_label,
)
from app.llm.graphs.img_diag_scope_intent import (
    ImgDiagScopeDraft,
    apply_scope_patch,
    build_scope_intent_text,
    confirmed_scope_from_draft,
    missing_required_scope_fields,
    parse_img_diag_scope_draft,
    should_trigger_scope_hitl,
)
from app.llm.graphs.img_diag_scope_validate import validate_scope_in_catalog
from app.llm.graphs.img_diag_session_store import (
    create_img_diag_resume_token,
    delete_img_diag_resume_session,
    get_img_diag_resume_session,
)
from app.llm.prompt_registry import PromptTemplateRegistry

logger = get_logger(__name__)


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
    human_prompt: str
    human_suggested_actions: list[str]
    missing_fields: list[str]
    interrupt_reason: str


def _cfg():
    return get_app_config().analysis


def _max_hitl_rounds() -> int:
    return max(1, int(getattr(_cfg(), "img_diag_scope_hitl_max_rounds", 5)))


def _draft_from_state(state: ImgDiagScopeGraphState) -> ImgDiagScopeDraft:
    draft_dict = state.get("scope_draft") or {}
    return ImgDiagScopeDraft(
        boiler=draft_dict.get("boiler"),
        device_name=draft_dict.get("device_name"),
        piperow_name=draft_dict.get("piperow_name"),
        row_no=draft_dict.get("row_no"),
        tube_no=draft_dict.get("tube_no"),
        confidence=draft_dict.get("confidence", "high"),
        confidence_reasons=tuple(draft_dict.get("confidence_reasons") or ()),
        time_meta=parse_img_diag_scope_draft("").time_meta,
    )


def _build_interrupt_payload(state: ImgDiagScopeGraphState) -> dict[str, Any]:
    draft = state.get("scope_draft") or {}
    scope_draft = {
        "boiler": draft.get("boiler"),
        "device_name": draft.get("device_name"),
        "piperow_name": draft.get("piperow_name"),
        "row_no": draft.get("row_no"),
        "tube_no": draft.get("tube_no"),
    }
    missing = state.get("missing_fields") or []
    return {
        "prompt": state.get("human_prompt") or "请补充或确认机组与受热面信息",
        "scope_draft": scope_draft,
        "scope_draft_display": scope_draft_to_display(scope_draft),
        "missing_fields": [scope_field_label(f) for f in missing],
        "validation_error": state.get("validation_error"),
        "suggested_actions": state.get("human_suggested_actions")
        or ["confirm_scope", "edit_scope", "abort"],
        "request_id": state.get("request_id"),
        "interrupt_reason": state.get("interrupt_reason"),
    }


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
    supplement = str(payload.get("user_supplement") or "").strip()
    if supplement:
        cumulative = (state.get("scope_cumulative_text") or state.get("query") or "").strip()
        state["scope_cumulative_text"] = f"{cumulative}\n{supplement}".strip()
    patch = payload.get("scope_patch")
    if isinstance(patch, dict) and state.get("scope_draft"):
        patch = normalize_scope_patch_keys(patch)
        dd = state["scope_draft"]
        tm = parse_img_diag_scope_draft(state.get("scope_cumulative_text") or "").time_meta
        draft = ImgDiagScopeDraft(
            boiler=dd.get("boiler"),
            device_name=dd.get("device_name"),
            piperow_name=dd.get("piperow_name"),
            row_no=dd.get("row_no"),
            tube_no=dd.get("tube_no"),
            confidence=dd.get("confidence", "high"),
            confidence_reasons=tuple(dd.get("confidence_reasons") or ()),
            time_meta=tm,
        )
        state["scope_draft"] = apply_scope_patch(draft, patch).to_dict()
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
        draft = parse_img_diag_scope_draft(
            cumulative,
            llm_client=client,
            prompt_registry=prompts,
        )
        state["scope_draft"] = draft.to_dict()
        state["scope_confidence"] = draft.confidence
        state["missing_fields"] = missing_required_scope_fields(draft)
        trigger, reason = should_trigger_scope_hitl(draft)
        state["interrupt_reason"] = reason if trigger else ""
        if trigger:
            state["human_prompt"] = (
                f"请补充以下信息：{format_missing_fields_cn(state['missing_fields'])}"
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
        scope = {
            "boiler": draft_dict.get("boiler"),
            "device_name": draft_dict.get("device_name"),
            "piperow_name": draft_dict.get("piperow_name"),
        }
        count, err = await validate_scope_in_catalog(scope)
        if err and count <= 0:
            state["validation_error"] = err
            state["needs_db_retry"] = True
            state["human_prompt"] = SCOPE_HITL_DB_NOT_MATCHED_PROMPT
            state["interrupt_reason"] = "db_validate_error"
            return state
        if count <= 0:
            state["validation_error"] = "scope_not_found_in_catalog"
            state["needs_db_retry"] = True
            state["human_prompt"] = SCOPE_HITL_DB_NOT_MATCHED_PROMPT
            state["interrupt_reason"] = "db_validate_zero_rows"
            return state
        draft = parse_img_diag_scope_draft(state.get("scope_cumulative_text") or "")
        merged = apply_scope_patch(
            draft,
            {
                "boiler": draft_dict.get("boiler"),
                "device_name": draft_dict.get("device_name"),
                "piperow_name": draft_dict.get("piperow_name"),
                "row_no": draft_dict.get("row_no"),
                "tube_no": draft_dict.get("tube_no"),
            },
        )
        state["confirmed_scope_intent"] = confirmed_scope_from_draft(merged)
        state["scope_intent_text"] = build_scope_intent_text(
            merged,
            scope_question=state.get("scope_cumulative_text") or "",
        )
        state["needs_db_retry"] = False
        state["validation_error"] = None
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
    g.add_edge("scope_human_confirm", "scope_preflight_llm")
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
        return {
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
        }

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
        initial = self._build_initial_state(request_id=rid, img_diag_request=img_diag_request)
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
                    thread_id=rid,
                    request_id=rid,
                    user_id=str(initial.get("user_id") or ""),
                    session_id=str(initial.get("session_id") or ""),
                    analysis_type=str(initial.get("analysis_type") or ""),
                    img_diag_subtype=str(initial.get("img_diag_subtype") or ""),
                    interrupt_payload=intr,
                    img_diag_request=img_diag_request,
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
            return {
                "status": "confirmed",
                "request_id": rid,
                "confirmed_scope_intent": final_state["confirmed_scope_intent"],
                "scope_intent_text": final_state["scope_intent_text"],
            }
        return {"status": "error", "message": "scope confirm incomplete"}

    async def resume_until_confirmed_or_interrupt(
        self,
        *,
        resume_token: str,
        user_id: str,
        session_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session = get_img_diag_resume_session(resume_token)
        if session is None:
            return {"status": "error", "message": "invalid or expired resume_token"}
        if session.user_id != user_id or session.session_id != session_id:
            return {"status": "error", "message": "resume_token session mismatch"}
        if not self.available():
            return {"status": "error", "message": "checkpoint not enabled"}

        try:
            from langgraph.types import Command  # type: ignore[import-not-found]
        except ImportError:
            return {"status": "error", "message": "langgraph Command unavailable"}

        config = img_diag_graph_configurable(session.thread_id)
        human_input = {"action": action, "payload": payload or {}}
        final_state: dict[str, Any] = {}

        async for ev in self._yield_updates(
            input_state=Command(resume=human_input),
            config=config,
        ):
            if "_state_update" in ev:
                final_state.update(ev["_state_update"])
            if "_interrupt_payload" in ev:
                intr = ev["_interrupt_payload"]
                new_token = create_img_diag_resume_token(
                    thread_id=session.thread_id,
                    request_id=session.request_id,
                    user_id=user_id,
                    session_id=session_id,
                    analysis_type=session.analysis_type,
                    img_diag_subtype=session.img_diag_subtype,
                    interrupt_payload=intr,
                    img_diag_request=session.img_diag_request,
                )
                delete_img_diag_resume_session(resume_token)
                return {
                    "status": "interrupt",
                    "request_id": session.request_id,
                    "resume_token": new_token,
                    "interrupt_payload": intr,
                    "img_diag_request": session.img_diag_request,
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
            stored_urls = (
                session.img_diag_request.get("image_urls")
                if isinstance(session.img_diag_request, dict)
                else []
            )
            url_count = len(
                [u for u in (stored_urls or []) if isinstance(u, str) and u.strip()]
            )
            logger.info(
                "img_diag scope resume confirmed request_id=%s action=%s "
                "session_image_urls url_count=%s raw_list_len=%s",
                session.request_id,
                action,
                url_count,
                len(stored_urls or []),
            )
            return {
                "status": "confirmed",
                "request_id": session.request_id,
                "confirmed_scope_intent": final_state["confirmed_scope_intent"],
                "scope_intent_text": final_state["scope_intent_text"],
                "img_diag_request": session.img_diag_request,
            }
        return {"status": "error", "message": "scope confirm incomplete after resume"}
