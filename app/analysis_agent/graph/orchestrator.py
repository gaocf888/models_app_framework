from __future__ import annotations

import asyncio
import time
from dataclasses import asdict
from typing import Any

from app.analysis_agent.agents.section_agent import section_result_to_slot_output, synthesize_section
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.nl2sql_executor import (
    plan_item_resolved,
    run_nl2sql_for_plan_item,
    task_status_from_rows,
)
from app.analysis_agent.plans.loader import effective_plan_version, plan_tasks_for_slot
from app.analysis_agent.renderers.charts_extra import chart_from_table
from app.analysis_agent.renderers.slot_renderer import render_deterministic_slot
from app.analysis_agent.slots.kinds import AnalysisAgentSlot, SlotOutput
from app.analysis_agent.slots.serialize import slot_from_dict
from app.conversation.manager import ConversationManager
from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import (
    ANALYSIS_AGENT_DEGRADE_COUNT,
    ANALYSIS_AGENT_NL2SQL_CALL_COUNT,
    ANALYSIS_AGENT_SLOT_LATENCY,
)
from app.llm.client import VLLMHttpClient
from app.llm.prompt_registry import PromptTemplateRegistry
from app.rag.hybrid_rag_service import HybridRAGService
from app.rag.service_registry import get_hybrid_rag_service
from app.services.nl2sql_service import NL2SQLService

logger = get_logger(__name__)


def _chunk_text(text: str, chunk_size: int) -> list[str]:
    if not text:
        return []
    size = max(1, chunk_size)
    return [text[i : i + size] for i in range(0, len(text), size)]


def _append_events(state: AnalysisAgentState, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    pending = state.setdefault("pending_events", [])
    pending.extend(events)


class SlotOrchestrator:
    """槽位 NL2SQL / 合成 / SSE 事件生成（供 LangGraph 节点调用）。"""

    def __init__(
        self,
        *,
        conv_manager: ConversationManager | None = None,
        llm_client: VLLMHttpClient | None = None,
        prompt_registry: PromptTemplateRegistry | None = None,
        hybrid_rag: HybridRAGService | None = None,
        nl2sql_service: NL2SQLService | None = None,
    ) -> None:
        self._conv = conv_manager or ConversationManager()
        self._llm = llm_client or VLLMHttpClient()
        self._prompts = prompt_registry or PromptTemplateRegistry()
        self._hybrid_rag = hybrid_rag or get_hybrid_rag_service()
        self._nl2sql = nl2sql_service or NL2SQLService(conv_manager=self._conv)
        self._cfg = get_app_config().analysis_agent

    def fetch_business_rag(self, analysis_type: str, query: str, top_k: int) -> list[str]:
        if not query.strip():
            return []
        try:
            q = f"{analysis_type} {query}".strip()
            return list(self._hybrid_rag.retrieve(q, top_k=top_k) or [])[:top_k]
        except Exception:  # noqa: BLE001
            logger.warning("analysis_agent rag_enrichment failed", exc_info=True)
            return []

    def run_intent_rag(self, state: AnalysisAgentState) -> AnalysisAgentState:
        """意图/业务 RAG：写入 context_snippets 与 intent_context（供章节 Agent 使用）。"""
        opts = state.get("options") or {}
        if not opts.get("enable_rag", True):
            state["intent_context"] = []
            return state
        analysis_type = str(state.get("analysis_type") or "")
        query = str(state.get("query") or "")
        top_k = int(opts.get("intent_rag_top_k") or self._cfg.rag_top_k)
        snippets = self.fetch_business_rag(analysis_type, query, top_k)
        state["context_snippets"] = snippets
        state["intent_context"] = list(snippets)
        return state

    def current_slot(self, state: AnalysisAgentState) -> AnalysisAgentSlot:
        ordered = state.get("ordered_slots") or []
        idx = int(state.get("slot_index") or 0)
        if idx >= len(ordered):
            raise IndexError("slot_index out of range")
        return slot_from_dict(ordered[idx])

    def _plan_mandatory_map(self, state: AnalysisAgentState) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for t in state.get("plan_tasks") or []:
            if isinstance(t, dict) and t.get("item_id"):
                out[str(t["item_id"])] = bool(t.get("mandatory"))
        return out

    async def run_slot_nl2sql(self, state: AnalysisAgentState) -> AnalysisAgentState:
        slot = self.current_slot(state)
        plan_tasks = state.get("plan_tasks") or []
        events = await self._acquire_slot_data(state, slot, plan_tasks)
        _append_events(state, events)
        state["slot_retry_nl2sql"] = False
        return state

    def run_slot_quality(self, state: AnalysisAgentState) -> AnalysisAgentState:
        slot = self.current_slot(state)
        opts = state.get("options") or {}
        strict = bool(opts.get("strict"))
        hitl = bool(opts.get("enable_human_in_the_loop", True)) and self._cfg.enable_human_in_the_loop
        mandatory_map = self._plan_mandatory_map(state)
        task_status = state.get("task_status") or {}
        state["needs_human_interrupt"] = False
        state["slot_retry_nl2sql"] = False
        state["abort_requested"] = False

        failed_mandatory: list[str] = []
        for iid in slot.source_item_ids:
            if not mandatory_map.get(iid):
                continue
            st = task_status.get(iid, "")
            if st in ("mandatory_failed", "mandatory_empty"):
                failed_mandatory.append(iid)

        if not failed_mandatory:
            return state

        retry_key = f"_nl2sql_retries_{slot.id}"
        retries = int(state.get(retry_key) or 0)
        max_retries = max(0, int(slot.max_nl2sql_retries or self._cfg.slot_nl2sql_max_retries))
        if retries < max_retries:
            ANALYSIS_AGENT_DEGRADE_COUNT.labels(reason="slot_nl2sql_retry").inc()
            state[retry_key] = retries + 1
            state["slot_retry_nl2sql"] = True
            gathered = state.get("gathered_data") or {}
            task_status = state.get("task_status") or {}
            for iid in slot.source_item_ids:
                gathered.pop(iid, None)
                task_status.pop(iid, None)
            return state

        if hitl and (slot.allow_human_confirm or failed_mandatory):
            ANALYSIS_AGENT_DEGRADE_COUNT.labels(reason="human_interrupt").inc()
            state["needs_human_interrupt"] = True
            state["human_prompt"] = (
                f"槽位 {slot.id} 关键数据缺失或查询失败：{', '.join(failed_mandatory)}。"
                "请选择继续策略。"
            )
            state["human_suggested_actions"] = ["retry", "skip_slot", "abort", "widen_time_range"]
            return state

        if strict:
            state["abort_requested"] = True
            state["error"] = f"mandatory data missing for slot {slot.id}: {failed_mandatory}"
        return state

    def apply_human_response(self, state: AnalysisAgentState, response: dict[str, Any]) -> AnalysisAgentState:
        action = str(response.get("action") or "skip_slot")
        payload = response.get("payload") or {}
        slot = self.current_slot(state)
        gathered = state.setdefault("gathered_data", {})
        task_status = state.setdefault("task_status", {})

        if action == "abort":
            state["abort_requested"] = True
            state["error"] = str(payload.get("reason") or "user aborted")
            return state

        if action == "widen_time_range":
            tr = payload.get("time_range")
            if isinstance(tr, dict):
                opts = state.setdefault("options", {})
                opts["time_range"] = tr
                q = state.get("query") or ""
                state["query"] = f"{q}（时间范围已调整）"
            for iid in slot.source_item_ids:
                gathered.pop(iid, None)
                task_status.pop(iid, None)
            state["slot_retry_nl2sql"] = True
            return state

        if action == "retry":
            for iid in slot.source_item_ids:
                gathered.pop(iid, None)
                task_status.pop(iid, None)
            state["slot_retry_nl2sql"] = True
            return state

        if action == "skip_slot":
            state["slot_skipped"] = True
            return state

        return state

    async def run_slot_synthesize(self, state: AnalysisAgentState) -> AnalysisAgentState:
        if state.get("slot_skipped"):
            state["slot_skipped"] = False
            state["_last_slot_output"] = asdict(
                SlotOutput(self.current_slot(state).id, self.current_slot(state).kind, "", "（本槽已跳过）\n\n")
            )
            state["_last_stream_chunks"] = []
            return state

        slot = self.current_slot(state)
        out, chunks = await self._synthesize_slot(state, slot)
        state["_last_slot_output"] = {
            "slot_id": out.slot_id,
            "kind": out.kind,
            "title": out.title,
            "markdown": out.markdown,
            "table": out.table,
            "chart": out.chart,
            "charts": out.charts,
            "error": out.error,
        }
        state["_last_stream_chunks"] = chunks
        return state

    def run_slot_emit(self, state: AnalysisAgentState) -> AnalysisAgentState:
        slot = self.current_slot(state)
        raw = state.get("_last_slot_output") or {}
        out = SlotOutput(
            slot_id=raw.get("slot_id", slot.id),
            kind=raw.get("kind", slot.kind),
            title=raw.get("title", slot.title),
            markdown=raw.get("markdown", ""),
            table=raw.get("table"),
            chart=raw.get("chart"),
            charts=raw.get("charts") or [],
            error=raw.get("error"),
        )
        chunks = list(state.get("_last_stream_chunks") or [])
        idx = int(state.get("slot_index") or 0)
        events = self._emit_slot_output(state, slot, out, chunks, idx)
        _append_events(state, events)
        state["slot_index"] = idx + 1
        return state

    def build_final_result(self, state: AnalysisAgentState) -> dict[str, Any]:
        summary = "".join(state.get("summary_parts") or [])
        nl2sql_calls = state.get("nl2sql_calls") or []
        cache_hits = sum(1 for c in nl2sql_calls if c.get("cache_hit"))
        return {
            "request_id": state.get("request_id"),
            "analysis_type": state.get("analysis_type"),
            "summary": summary,
            "structured_report": state.get("structured_report") or {},
            "evidence": {
                "nl2sql_calls": nl2sql_calls,
                "nl2sql_cache_hits": cache_hits,
                "data_coverage": {
                    "mode": "analysis_agent",
                    "task_status": state.get("task_status"),
                },
                "used_rag": bool(state.get("context_snippets")),
                "human_interactions": state.get("human_interactions") or [],
            },
            "trace": {
                **(state.get("trace") or {}),
                "slot_trace": state.get("slot_trace") or [],
                "from_report_spec": bool(state.get("from_report_spec")),
                "orchestrator": "langgraph_slot_pipeline",
                "checkpoint_thread_id": state.get("_checkpoint_thread_id"),
            },
        }

    async def _acquire_slot_data(
        self,
        state: AnalysisAgentState,
        slot: AnalysisAgentSlot,
        plan_tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        user_id = state["user_id"]
        session_id = state["session_id"]
        request_id = state["request_id"]
        options = state.get("options") or {}
        max_rows = int(options.get("max_rows_per_query") or 2000)
        gathered = state.setdefault("gathered_data", {})
        task_status = state.setdefault("task_status", {})
        nl2sql_calls = state.setdefault("nl2sql_calls", [])
        max_retries = max(0, int(slot.max_nl2sql_retries or self._cfg.slot_nl2sql_max_retries))
        slot_plan = plan_tasks_for_slot(plan_tasks, slot.source_item_ids)
        if not slot_plan:
            return events

        analysis_type = str(state.get("analysis_type") or "overheat_guidance")
        merged_opts = dict(options)
        trace_ptv = (state.get("trace") or {}).get("plan_template_version")
        if trace_ptv:
            merged_opts["plan_template_version"] = trace_ptv
        plan_version = effective_plan_version(analysis_type, merged_opts)
        user_query = str(state.get("query") or "")

        async def _one(task: dict[str, Any]) -> None:
            item_id = str(task["item_id"])
            if plan_item_resolved(item_id, gathered_data=gathered, task_status=task_status):
                rows = list(gathered.get(item_id) or [])
                nl2sql_calls.append(
                    {
                        "item_id": item_id,
                        "row_count": len(rows),
                        "latency_ms": 0,
                        "analysis_type": analysis_type,
                        "plan_template_version": plan_version,
                        "cache_hit": True,
                        "slot_id": slot.id,
                    }
                )
                events.append(
                    {
                        "event": "analysis_agent_nl2sql_done",
                        "item_id": item_id,
                        "row_count": len(rows),
                        "latency_ms": 0,
                        "slot_id": slot.id,
                        "cached": True,
                    }
                )
                return
            question = str(task.get("question") or "")
            mandatory = bool(task.get("mandatory", False))
            last_err: str | None = None
            for attempt in range(max_retries + 1):
                try:
                    rows, call_rec = await run_nl2sql_for_plan_item(
                        nl2sql=self._nl2sql,
                        user_id=user_id,
                        session_id=session_id,
                        question=question,
                        item_id=item_id,
                        analysis_type=analysis_type,
                        plan_template_version=plan_version,
                        analysis_request_id=request_id,
                        query=user_query,
                    )
                    rows = rows[:max_rows]
                    call_rec["attempt"] = attempt
                    call_rec["slot_id"] = slot.id
                    nl2sql_calls.append(call_rec)
                    gathered[item_id] = rows
                    task_status[item_id] = task_status_from_rows(item_id, rows, mandatory=mandatory)
                    ANALYSIS_AGENT_NL2SQL_CALL_COUNT.labels(
                        analysis_type=analysis_type, status="success"
                    ).inc()
                    events.append(
                        {
                            "event": "analysis_agent_nl2sql_done",
                            "item_id": item_id,
                            "row_count": len(rows),
                            "latency_ms": call_rec.get("latency_ms"),
                            "slot_id": slot.id,
                            "cached": False,
                        }
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    last_err = str(exc)
                    logger.warning(
                        "analysis_agent nl2sql failed item=%s attempt=%s: %s",
                        item_id,
                        attempt,
                        exc,
                    )
            gathered[item_id] = []
            task_status[item_id] = task_status_from_rows(
                item_id, [], mandatory=mandatory, error=last_err
            )
            ANALYSIS_AGENT_NL2SQL_CALL_COUNT.labels(
                analysis_type=analysis_type, status="failed"
            ).inc()

        await asyncio.gather(*[_one(t) for t in slot_plan])
        return events

    async def _synthesize_slot(
        self,
        state: AnalysisAgentState,
        slot: AnalysisAgentSlot,
    ) -> tuple[SlotOutput, list[str]]:
        options = state.get("options") or {}
        chart_mode = str(options.get("chart_mode") or "auto")
        gathered = state.get("gathered_data") or {}
        task_status = state.get("task_status") or {}
        context_snippets = list(state.get("intent_context") or state.get("context_snippets") or [])
        query = state.get("query") or ""

        analysis_type = str(state.get("analysis_type") or "overheat_guidance")
        t_slot = time.perf_counter()
        if slot.kind in ("llm_narrative", "llm_section"):
            use_react = bool(options.get("use_react_agent", self._cfg.use_react_agent))
            result = await synthesize_section(
                prompts=self._prompts,
                slot=slot,
                query=query,
                gathered_data=gathered,
                context_snippets=context_snippets,
                task_status=task_status,
                hybrid_rag=self._hybrid_rag,
                analysis_type=analysis_type,
                llm_client=self._llm,
                use_react_agent=use_react,
                intent_context=list(state.get("intent_context") or []),
            )
            out, chunks = section_result_to_slot_output(slot, result)
            ANALYSIS_AGENT_SLOT_LATENCY.labels(
                slot_kind=slot.kind, analysis_type=analysis_type
            ).observe(time.perf_counter() - t_slot)
            return out, chunks

        out = render_deterministic_slot(
            slot=slot,
            gathered_data=gathered,
            task_status=task_status,
            chart_mode=chart_mode,
        )
        ANALYSIS_AGENT_SLOT_LATENCY.labels(
            slot_kind=slot.kind, analysis_type=analysis_type
        ).observe(time.perf_counter() - t_slot)
        return out, _chunk_text(out.markdown, self._cfg.stream_chunk_chars)

    def _emit_slot_output(
        self,
        state: AnalysisAgentState,
        slot: AnalysisAgentSlot,
        out: SlotOutput,
        stream_chunks: list[str],
        slot_index: int,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        options = state.get("options") or {}
        chart_mode = str(options.get("chart_mode") or "auto")

        events.append(
            {
                "event": "analysis_agent_slot_start",
                "slot_id": slot.id,
                "slot_index": slot_index,
                "kind": slot.kind,
            }
        )

        if stream_chunks and slot.kind in ("llm_narrative", "llm_section"):
            for piece in stream_chunks:
                events.append({"event": "analysis_agent_summary_delta", "text": piece})
        else:
            for piece in _chunk_text(out.markdown, self._cfg.stream_chunk_chars):
                events.append({"event": "analysis_agent_summary_delta", "text": piece})

        if out.table and self._cfg.enable_structured_sse_events:
            events.append(
                {"event": "analysis_agent_table_payload", "table": out.table, "slot_id": slot.id}
            )
            if slot.chart_when_table and chart_mode != "off":
                extra = chart_from_table(
                    table_id=out.table.get("id") or slot.table_id,
                    table_kind=slot.table_kind,
                    table=out.table,
                    title=slot.title,
                )
                if extra:
                    events.append(
                        {"event": "analysis_agent_chart_payload", "chart": extra, "slot_id": slot.id}
                    )
                    out.charts = list(out.charts) + [extra]

        for ch in out.charts:
            events.append(
                {"event": "analysis_agent_chart_payload", "chart": ch, "slot_id": slot.id}
            )

        state.setdefault("summary_parts", []).append(out.markdown)
        sections = state.setdefault("structured_report", {}).setdefault("sections", [])
        if out.title and out.markdown.strip():
            sections.append(
                {"title": out.title, "content": out.markdown.strip(), "slot_id": slot.id}
            )
        if out.table:
            state.setdefault("structured_report", {}).setdefault("tables", []).append(out.table)
        for ch in out.charts:
            state.setdefault("structured_report", {}).setdefault("charts", []).append(ch)

        state.setdefault("slot_trace", []).append(
            {
                "slot_id": slot.id,
                "kind": slot.kind,
                "chars": len(out.markdown),
                "error": out.error,
            }
        )
        events.append({"event": "analysis_agent_slot_complete", "slot_id": slot.id})
        return events
