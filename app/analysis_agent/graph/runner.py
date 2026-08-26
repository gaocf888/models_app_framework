from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.analysis_agent.checkpoint import graph_configurable
from app.analysis_agent.context_loader import load_analysis_run_context
from app.analysis_agent.graph.builder import build_analysis_agent_graph
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.plans.loader import effective_plan_version
from app.analysis_agent.session_store import create_resume_token, delete_resume_session, get_resume_session
from app.analysis_agent.slots.registry import registry_available
from app.analysis_agent.slots.serialize import slot_to_dict
from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import ANALYSIS_AGENT_REQUEST_COUNT
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.rag.hybrid_rag_service import HybridRAGService
from app.services.analysis_agent_stream_control import AnalysisAgentStreamControl
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)

CancelChecker = Callable[[], Awaitable[bool]]


def _new_request_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"aa_{ts}_{uuid.uuid4().hex[:8]}"


def _result_status_label(result: dict[str, Any]) -> str:
    status = str((result.get("trace") or {}).get("status") or "success")
    if status in {"aborted", "failed"}:
        return status
    return "success"


class AnalysisAgentGraphRunner:
    """
    综合分析智能体：LangGraph 多节点槽流水线（checkpoint + interrupt/resume）。
    无 checkpointer 时降级为 orchestrator 顺序执行（不支持 HITL）。
    """

    def __init__(
        self,
        *,
        conv_manager: ConversationManager | None = None,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
        hybrid_rag: HybridRAGService | None = None,
        nl2sql_service: NL2SQLService | None = None,
        stream_control: AnalysisAgentStreamControl | None = None,
    ) -> None:
        self._orch = SlotOrchestrator(
            conv_manager=conv_manager,
            llm_client=llm_client,
            prompt_registry=prompt_registry,
            hybrid_rag=hybrid_rag,
            nl2sql_service=nl2sql_service,
        )
        self._cfg = get_app_config().analysis_agent
        self._stream_ctrl = stream_control or AnalysisAgentStreamControl()
        self._graph = None
        self._checkpointer = None
        if self._cfg.use_langgraph:
            self._graph, self._checkpointer = build_analysis_agent_graph(self._orch)

    def _build_stream_cancel_checker(
        self, user_id: str, session_id: str, stream_id: str | None
    ) -> CancelChecker | None:
        if not stream_id:
            return None

        async def _check() -> bool:
            return await self._stream_ctrl.is_cancelled(user_id, session_id, stream_id)

        return _check

    def _build_initial_state(
        self,
        *,
        request_id: str,
        user_id: str,
        session_id: str,
        analysis_type: str,
        query: str,
        options: dict[str, Any],
        stream_id: str | None = None,
        cancel_checker: CancelChecker | None = None,
    ) -> AnalysisAgentState:
        thread_id = request_id
        state: AnalysisAgentState = {
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
            "analysis_type": analysis_type,
            "query": query,
            "options": options,
            "_checkpoint_thread_id": thread_id,
            "pending_events": [],
        }
        if stream_id:
            state["_stream_id"] = stream_id
        if cancel_checker is not None:
            state["_cancel_checker"] = cancel_checker
        return state

    def _drain_pending(self, update: dict[str, Any]) -> list[dict[str, Any]]:
        events = list(update.get("pending_events") or [])
        update["pending_events"] = []
        return events

    async def _yield_graph_updates(
        self,
        *,
        input_state: AnalysisAgentState | Any,
        config: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        assert self._graph is not None
        # updates：节点结束 pending_events；custom：synthesize 中 get_stream_writer 真流式
        try:
            stream = self._graph.astream(
                input_state, config, stream_mode=["updates", "custom"]
            )
        except TypeError:
            stream = self._graph.astream(input_state, config, stream_mode="updates")

        async for item in stream:
            mode = "updates"
            chunk: Any = item
            if isinstance(item, tuple) and len(item) == 2:
                mode, chunk = item
            if mode == "custom":
                if isinstance(chunk, dict) and chunk.get("event"):
                    yield chunk
                continue
            if mode != "updates" or not isinstance(chunk, dict):
                continue

            if "__interrupt__" in chunk:
                for intr in chunk["__interrupt__"]:
                    payload = intr.value if hasattr(intr, "value") else intr
                    if isinstance(payload, dict):
                        yield {"_interrupt_payload": payload}
                continue
            for _node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                for ev in self._drain_pending(update):
                    yield ev

        snapshot = await self._graph.aget_state(config)
        if snapshot is not None and getattr(snapshot, "interrupts", None):
            for intr in snapshot.interrupts:
                val = intr.value if hasattr(intr, "value") else intr
                if isinstance(val, dict):
                    yield {"_interrupt_payload": val}

    async def iter_stream_events(
        self,
        *,
        user_id: str,
        session_id: str,
        analysis_type: str,
        query: str,
        options: dict[str, Any] | None = None,
        on_complete: Callable[[dict[str, Any]], Any] | None = None,
        request_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if not self._cfg.enabled:
            ANALYSIS_AGENT_REQUEST_COUNT.labels(
                analysis_type=analysis_type, status="failed"
            ).inc()
            yield {"event": "analysis_agent_error", "message": "analysis_agent disabled"}
            return
        if not registry_available(analysis_type):
            ANALYSIS_AGENT_REQUEST_COUNT.labels(
                analysis_type=analysis_type, status="failed"
            ).inc()
            yield {
                "event": "analysis_agent_error",
                "message": f"unsupported analysis_type:{analysis_type}",
            }
            return

        opts = dict(options or {})
        opts["plan_template_version"] = effective_plan_version(analysis_type, opts)
        rid = request_id or _new_request_id()
        stream_id = self._stream_ctrl.begin_stream(user_id, session_id)
        cancel_checker = self._build_stream_cancel_checker(user_id, session_id, stream_id)
        ANALYSIS_AGENT_REQUEST_COUNT.labels(
            analysis_type=analysis_type, status="started"
        ).inc()
        yield {
            "event": "started",
            "stream_id": stream_id,
            "request_id": rid,
        }
        initial = self._build_initial_state(
            request_id=rid,
            user_id=user_id,
            session_id=session_id,
            analysis_type=analysis_type,
            query=query,
            options=opts,
            stream_id=stream_id,
            cancel_checker=cancel_checker,
        )

        try:
            if self._graph is None or self._checkpointer is None:
                async for ev in self._iter_sequential_fallback(
                    initial=initial,
                    on_complete=on_complete,
                ):
                    yield ev
                return

            config = graph_configurable(rid)
            async for ev in self._yield_graph_updates(input_state=initial, config=config):
                if "_interrupt_payload" in ev:
                    payload = ev["_interrupt_payload"]
                    token = create_resume_token(
                        thread_id=rid,
                        request_id=rid,
                        user_id=user_id,
                        session_id=session_id,
                        analysis_type=analysis_type,
                        interrupt_payload=payload,
                    )
                    yield {
                        "event": "analysis_agent_user_input_required",
                        "request_id": rid,
                        "resume_token": token,
                        "slot_id": payload.get("slot_id"),
                        "prompt": payload.get("prompt"),
                        "suggested_actions": payload.get("suggested_actions", []),
                    }
                    return
                yield ev
                if ev.get("event") == "analysis_agent_finished":
                    result = ev.get("result") or {}
                    status_label = _result_status_label(result)
                    ANALYSIS_AGENT_REQUEST_COUNT.labels(
                        analysis_type=analysis_type, status=status_label
                    ).inc()
                    if on_complete:
                        maybe = on_complete(result)
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    return
        except Exception as exc:  # noqa: BLE001
            logger.exception("analysis_agent graph stream failed")
            ANALYSIS_AGENT_REQUEST_COUNT.labels(
                analysis_type=analysis_type, status="failed"
            ).inc()
            yield {"event": "analysis_agent_error", "message": str(exc)}
        finally:
            await self._stream_ctrl.clear_stream(user_id, session_id, stream_id)

    async def iter_resume_stream_events(
        self,
        *,
        resume_token: str,
        user_id: str,
        session_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        on_complete: Callable[[dict[str, Any]], Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        session = get_resume_session(resume_token)
        if session is None:
            yield {"event": "analysis_agent_error", "message": "invalid or expired resume_token"}
            return
        if session.user_id != user_id or session.session_id != session_id:
            yield {"event": "analysis_agent_error", "message": "resume_token session mismatch"}
            return
        if self._graph is None or self._checkpointer is None:
            yield {"event": "analysis_agent_error", "message": "checkpoint not enabled"}
            return

        try:
            from langgraph.types import Command  # type: ignore[import-not-found]
        except ImportError:
            yield {"event": "analysis_agent_error", "message": "langgraph Command unavailable"}
            return

        config = graph_configurable(session.thread_id)
        human_input = {"action": action, "payload": payload or {}}

        try:
            async for ev in self._yield_graph_updates(
                input_state=Command(resume=human_input),
                config=config,
            ):
                if "_interrupt_payload" in ev:
                    intr = ev["_interrupt_payload"]
                    new_token = create_resume_token(
                        thread_id=session.thread_id,
                        request_id=session.request_id,
                        user_id=user_id,
                        session_id=session_id,
                        analysis_type=session.analysis_type,
                        interrupt_payload=intr,
                    )
                    delete_resume_session(resume_token)
                    yield {
                        "event": "analysis_agent_user_input_required",
                        "request_id": session.request_id,
                        "resume_token": new_token,
                        "slot_id": intr.get("slot_id"),
                        "prompt": intr.get("prompt"),
                        "suggested_actions": intr.get("suggested_actions", []),
                    }
                    return
                yield ev
                if ev.get("event") == "analysis_agent_finished":
                    delete_resume_session(resume_token)
                    result = ev.get("result") or {}
                    if on_complete:
                        maybe = on_complete(result)
                        if asyncio.iscoroutine(maybe):
                            await maybe
                    return
        except Exception as exc:  # noqa: BLE001
            logger.exception("analysis_agent resume stream failed")
            yield {"event": "analysis_agent_error", "message": str(exc)}

    async def resume_to_result(
        self,
        *,
        resume_token: str,
        user_id: str,
        session_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {}
        async for ev in self.iter_resume_stream_events(
            resume_token=resume_token,
            user_id=user_id,
            session_id=session_id,
            action=action,
            payload=payload,
        ):
            if ev.get("event") == "analysis_agent_finished":
                result = ev.get("result") or {}
            if ev.get("event") == "analysis_agent_error":
                raise RuntimeError(str(ev.get("message")))
        if not result:
            raise RuntimeError("resume completed without result")
        return result

    async def _iter_sequential_fallback(
        self,
        *,
        initial: AnalysisAgentState,
        on_complete: Callable[[dict[str, Any]], Any] | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """无 checkpoint 时的顺序槽流水线（不支持 interrupt）。"""
        state = dict(initial)
        analysis_type = state["analysis_type"]
        opts = state.get("options") or {}
        plan_version = effective_plan_version(analysis_type, opts)
        opts["plan_template_version"] = plan_version
        try:
            ctx = load_analysis_run_context(
                analysis_type, version=plan_version, prompts=self._orch._prompts
            )
            slots = ctx.slots
            plan_tasks = ctx.plan_tasks
        except ValueError as exc:
            ANALYSIS_AGENT_REQUEST_COUNT.labels(
                analysis_type=analysis_type, status="failed"
            ).inc()
            yield {"event": "analysis_agent_error", "message": str(exc)}
            return

        plan_version = ctx.plan_template_version
        opts["plan_template_version"] = plan_version
        state["ordered_slots"] = [slot_to_dict(s) for s in slots]
        state["plan_tasks"] = plan_tasks
        state["from_report_spec"] = ctx.from_report_spec
        if ctx.report_title:
            state["report_title"] = ctx.report_title
        state["slot_index"] = 0
        state["slots_total"] = len(slots)
        state["report_tables"] = list(ctx.report_tables or [])
        state["report_charts"] = list(ctx.report_charts or [])
        state.setdefault("gathered_data", {})
        state.setdefault("task_status", {})
        state.setdefault("nl2sql_calls", [])
        state.setdefault("intent_context", [])
        state.setdefault("summary_parts", [])
        state.setdefault("structured_report", {"sections": [], "tables": [], "charts": []})
        state.setdefault("slot_trace", [])
        state["trace"] = {
            "module": "analysis_agent",
            "orchestrator": "sequential_fallback",
            "plan_template_version": plan_version,
            "from_report_spec": ctx.from_report_spec,
        }

        self._orch.run_intent_rag(state)

        yield {
            "event": "analysis_agent_meta",
            "request_id": state["request_id"],
            "analysis_type": analysis_type,
            "slot_total": len(slots),
            "plan_items": len(plan_tasks),
            "from_report_spec": ctx.from_report_spec,
            "plan_template_version": plan_version,
        }

        t0 = time.perf_counter()
        # T1：全量取数 → 质量门（可重试）→ 按章合成（读缓存）
        state.setdefault("degrade_reasons", [])
        state.setdefault("_acquire_retries", 0)
        while True:
            if await self._orch._is_cancelled(state):
                state["abort_requested"] = True
                state["error"] = "user_cancelled"
                break
            await self._orch.run_acquire_data(state)
            for ev in list(state.pop("pending_events", []) or []):
                yield ev
            if state.get("abort_requested") and state.get("error") == "user_cancelled":
                break
            self._orch.run_data_quality(state)
            if state.get("abort_requested"):
                break
            if state.get("acquire_retry"):
                continue
            break

        if state.get("abort_requested"):
            result = self._orch.build_final_result(state)
            result["trace"]["total_ms"] = int((time.perf_counter() - t0) * 1000)
            result["trace"]["orchestrator"] = "sequential_fallback"
            state["_final_result"] = result
            if state.get("error") == "user_cancelled":
                yield {
                    "event": "analysis_agent_cancelled",
                    "request_id": state["request_id"],
                    "terminate_reason": "user_cancelled",
                    "stream_id": state.get("_stream_id"),
                }
                status_label = "aborted"
            else:
                yield {
                    "event": "analysis_agent_error",
                    "request_id": state["request_id"],
                    "message": state.get("error"),
                }
                status_label = "failed"
            yield {
                "event": "analysis_agent_report_complete",
                "request_id": state["request_id"],
                "summary_length": len(result.get("summary") or ""),
                "structured_report": result.get("structured_report"),
                "degrade_reasons": result.get("degrade_reasons") or [],
            }
            yield {
                "event": "analysis_agent_finished",
                "request_id": state["request_id"],
                "result": result,
            }
            ANALYSIS_AGENT_REQUEST_COUNT.labels(
                analysis_type=analysis_type, status=status_label
            ).inc()
            if on_complete:
                maybe = on_complete(result)
                if asyncio.iscoroutine(maybe):
                    await maybe
            return

        parallel = self._orch._resolve_chapter_synth_parallel(opts)
        if parallel > 1:
            await self._orch.run_chapter_pipeline(state)
            for ev in list(state.pop("pending_events", []) or []):
                yield ev
        else:
            for idx in range(len(slots)):
                if await self._orch._is_cancelled(state):
                    state["abort_requested"] = True
                    state["error"] = "user_cancelled"
                    break
                state["slot_index"] = idx
                self._orch.run_chapter_prepare(state)
                for ev in list(state.pop("pending_events", []) or []):
                    yield ev
                await self._orch.run_chapter_synthesize(state)
                for ev in list(state.pop("pending_events", []) or []):
                    yield ev
                if state.get("abort_requested"):
                    break
                self._orch.run_chapter_emit(state)
                for ev in list(state.pop("pending_events", []) or []):
                    yield ev
                if await self._orch._is_cancelled(state):
                    state["abort_requested"] = True
                    state["error"] = "user_cancelled"
                    break

        result = self._orch.build_final_result(state)
        result["trace"]["total_ms"] = int((time.perf_counter() - t0) * 1000)
        result["trace"]["orchestrator"] = "sequential_fallback"
        state["_final_result"] = result
        if state.get("error") == "user_cancelled":
            yield {
                "event": "analysis_agent_cancelled",
                "request_id": state["request_id"],
                "terminate_reason": "user_cancelled",
                "stream_id": state.get("_stream_id"),
            }
            status_label = "aborted"
        elif state.get("abort_requested") and state.get("error"):
            status_label = "failed"
        else:
            status_label = "success"
        yield {
            "event": "analysis_agent_report_complete",
            "request_id": state["request_id"],
            "summary_length": len(result.get("summary") or ""),
            "structured_report": result.get("structured_report"),
            "degrade_reasons": result.get("degrade_reasons") or [],
        }
        yield {"event": "analysis_agent_finished", "request_id": state["request_id"], "result": result}
        ANALYSIS_AGENT_REQUEST_COUNT.labels(
            analysis_type=analysis_type, status=status_label
        ).inc()
        if on_complete:
            maybe = on_complete(result)
            if asyncio.iscoroutine(maybe):
                await maybe