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
from app.analysis_agent.quality import check_l1_anchors, resolve_quality_profile
from app.analysis_agent.plans.loader import effective_plan_version, plan_tasks_for_slot
from app.analysis_agent.renderers.charts_extra import chart_from_table
from app.analysis_agent.renderers.configured_viz import prepare_chapter_viz
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
            state["context_snippets"] = []
            state["intent_context"] = []
            return state
        analysis_type = str(state.get("analysis_type") or "")
        query = str(state.get("query") or "")
        top_k = int(opts.get("intent_rag_top_k") or self._cfg.rag_top_k)
        snippets = self.fetch_business_rag(analysis_type, query, top_k)
        state["context_snippets"] = snippets
        state["intent_context"] = list(snippets)
        return state

    def _resolve_chapter_synth_parallel(self, options: dict[str, Any]) -> int:
        raw = options.get("chapter_synth_max_parallel")
        if raw is None:
            raw = self._cfg.chapter_synth_max_parallel
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = 1
        return max(1, min(3, n))

    def _flush_pending_events_live(self, state: AnalysisAgentState) -> None:
        """LangGraph 单节点 chapter_pipeline 内：有 stream_writer 则即时推送，避免整管线结束才吐事件。"""
        events = list(state.pop("pending_events", []) or [])
        if not events:
            return
        writer = self._try_get_stream_writer()
        if writer is None:
            _append_events(state, events)
            return
        leftover: list[dict[str, Any]] = []
        for ev in events:
            try:
                writer(ev)
            except Exception:  # noqa: BLE001
                leftover.append(ev)
        if leftover:
            _append_events(state, leftover)

    async def run_chapter_pipeline(self, state: AnalysisAgentState) -> AnalysisAgentState:
        """
        按章 prepare → synthesize → emit。

        - parallel=1：串行（真流式可用）
        - parallel>1：有限并行合成（为保序 SSE，并行时关闭 live delta，合成后再顺序 emit）
        """
        total = int(state.get("slots_total") or len(state.get("ordered_slots") or []))
        if total <= 0:
            return state
        parallel = self._resolve_chapter_synth_parallel(state.get("options") or {})
        state.setdefault("trace", {})
        if isinstance(state["trace"], dict):
            state["trace"]["chapter_synth_max_parallel"] = parallel

        if parallel <= 1:
            for idx in range(total):
                if state.get("abort_requested"):
                    break
                if await self._is_cancelled(state):
                    state["abort_requested"] = True
                    state["error"] = "user_cancelled"
                    break
                state["slot_index"] = idx
                self.run_chapter_prepare(state)
                self._flush_pending_events_live(state)
                await self.run_chapter_synthesize(state)
                self._flush_pending_events_live(state)
                if state.get("abort_requested"):
                    break
                self.run_chapter_emit(state)
                self._flush_pending_events_live(state)
            return state

        # 并行合成：每章独立 work state，避免竞态；并行时关闭 live delta，合成后按序 emit
        options = dict(state.get("options") or {})
        options["_force_buffered_synth"] = True
        sem = asyncio.Semaphore(parallel)
        results: dict[int, AnalysisAgentState] = {}

        async def _one(idx: int) -> None:
            async with sem:
                if state.get("abort_requested"):
                    return
                if await self._is_cancelled(state):
                    state["abort_requested"] = True
                    state["error"] = "user_cancelled"
                    return
                work: AnalysisAgentState = {
                    "request_id": state.get("request_id"),
                    "user_id": state.get("user_id"),
                    "session_id": state.get("session_id"),
                    "analysis_type": state.get("analysis_type"),
                    "query": state.get("query"),
                    "options": options,
                    "ordered_slots": state.get("ordered_slots") or [],
                    "slots_total": total,
                    "slot_index": idx,
                    "gathered_data": state.get("gathered_data") or {},
                    "task_status": state.get("task_status") or {},
                    "intent_context": list(state.get("intent_context") or []),
                    "context_snippets": list(state.get("context_snippets") or []),
                    "report_tables": list(state.get("report_tables") or []),
                    "report_charts": list(state.get("report_charts") or []),
                    "pending_events": [],
                    "summary_parts": [],
                    "slot_trace": [],
                    "structured_report": {"sections": [], "tables": [], "charts": [], "suggestions": []},
                    "_cancel_checker": state.get("_cancel_checker"),
                    "_stream_id": state.get("_stream_id"),
                }
                self.run_chapter_prepare(work)
                await self.run_chapter_synthesize(work)
                if work.get("abort_requested"):
                    state["abort_requested"] = True
                    state["error"] = work.get("error")
                results[idx] = work

        await asyncio.gather(*(_one(i) for i in range(total)))

        for idx in range(total):
            if state.get("abort_requested"):
                break
            work = results.get(idx)
            if work is None:
                continue
            state["slot_index"] = idx
            state["_prepared_viz"] = work.get("_prepared_viz") or {}
            state["_last_slot_output"] = work.get("_last_slot_output") or {}
            state["_last_stream_chunks"] = list(work.get("_last_stream_chunks") or [])
            state["_narrative_live_streamed"] = False
            prep_events = [
                ev
                for ev in (work.get("pending_events") or [])
                if ev.get("event")
                in {"analysis_agent_table_payload", "analysis_agent_chart_payload"}
            ]
            if prep_events:
                _append_events(state, prep_events)
            self.run_chapter_emit(state)
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

    @staticmethod
    def _dedupe_plan_tasks(plan_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for t in plan_tasks:
            if not isinstance(t, dict) or not t.get("item_id"):
                continue
            iid = str(t["item_id"])
            if iid in seen:
                continue
            seen.add(iid)
            out.append(dict(t))
        return out

    @staticmethod
    def _dependency_ids(task: dict[str, Any]) -> list[str]:
        raw = task.get("dependency_ids") or []
        if not isinstance(raw, list):
            return []
        return [str(x).strip() for x in raw if str(x).strip()]

    async def run_acquire_data(self, state: AnalysisAgentState) -> AnalysisAgentState:
        """全量 plan 取数：按 dependency_ids 分层并行，写入 gathered_data。"""
        plan_tasks = self._dedupe_plan_tasks(list(state.get("plan_tasks") or []))
        state["plan_tasks"] = plan_tasks
        events = await self._acquire_all_plan_data(state, plan_tasks)
        _append_events(state, events)
        state["acquire_retry"] = False
        state["slot_retry_nl2sql"] = False
        return state

    async def run_slot_nl2sql(self, state: AnalysisAgentState) -> AnalysisAgentState:
        """兼容旧路径：单章取数（主图 T1 已改为 acquire_data，一般不再调用）。"""
        slot = self.current_slot(state)
        plan_tasks = state.get("plan_tasks") or []
        events = await self._acquire_slot_data(state, slot, plan_tasks)
        _append_events(state, events)
        state["slot_retry_nl2sql"] = False
        return state

    def run_data_quality(self, state: AnalysisAgentState) -> AnalysisAgentState:
        """
        全量质量门（T1 L0 + T3 L1）：无 HITL。
        L0：mandatory 空/失败 → 重试；耗尽后 strict 失败或降级继续写章。
        L1：用户原句关键锚点缺失 → degrade_reasons；strict_like+strict 可失败。
        """
        opts = state.get("options") or {}
        strict = bool(opts.get("strict"))
        mandatory_map = self._plan_mandatory_map(state)
        task_status = state.get("task_status") or {}
        # 用户 stop 优先：不清除 cancel 态
        if state.get("error") == "user_cancelled" and state.get("abort_requested"):
            return state

        state["needs_human_interrupt"] = False
        state["acquire_retry"] = False
        state["slot_retry_nl2sql"] = False
        state["abort_requested"] = False
        degrade = list(state.get("degrade_reasons") or [])

        # L1 锚点：整次请求只检一次
        if not state.get("_l1_anchor_checked"):
            state["_l1_anchor_checked"] = True
            profile = resolve_quality_profile(
                options=opts, cfg_profile=self._cfg.quality_profile
            )
            l1 = check_l1_anchors(
                query=str(state.get("query") or ""),
                analysis_type=str(state.get("analysis_type") or ""),
            )
            state["quality_l1"] = {
                "profile": profile,
                **l1,
            }
            for reason in l1.get("degrade_reasons") or []:
                if reason not in degrade:
                    degrade.append(reason)
                    ANALYSIS_AGENT_DEGRADE_COUNT.labels(reason=reason).inc()
            if profile == "strict_like" and strict and (l1.get("missing") or []):
                state["abort_requested"] = True
                state["error"] = f"l1 anchors missing: {l1.get('missing')}"
                degrade.append("l1_strict_abort")
                ANALYSIS_AGENT_DEGRADE_COUNT.labels(reason="l1_strict_abort").inc()
                state["degrade_reasons"] = degrade
                return state

        failed_mandatory = [
            iid
            for iid, mandatory in mandatory_map.items()
            if mandatory and task_status.get(iid) in ("mandatory_failed", "mandatory_empty")
        ]
        if not failed_mandatory:
            state["degrade_reasons"] = degrade
            return state

        retries = int(state.get("_acquire_retries") or 0)
        max_retries = max(0, int(self._cfg.acquire_max_retries))
        if retries < max_retries:
            ANALYSIS_AGENT_DEGRADE_COUNT.labels(reason="acquire_nl2sql_retry").inc()
            state["_acquire_retries"] = retries + 1
            state["acquire_retry"] = True
            state["slot_retry_nl2sql"] = True  # 兼容
            gathered = state.setdefault("gathered_data", {})
            for iid in failed_mandatory:
                gathered.pop(iid, None)
                task_status.pop(iid, None)
            # 依赖失败项的下游也清掉，便于下一轮分层重跑
            for t in state.get("plan_tasks") or []:
                if not isinstance(t, dict) or not t.get("item_id"):
                    continue
                deps = self._dependency_ids(t)
                if any(d in failed_mandatory for d in deps):
                    child = str(t["item_id"])
                    gathered.pop(child, None)
                    task_status.pop(child, None)
            state["task_status"] = task_status
            state["degrade_reasons"] = degrade
            return state

        if strict:
            state["abort_requested"] = True
            state["error"] = f"mandatory data missing after retries: {failed_mandatory}"
            ANALYSIS_AGENT_DEGRADE_COUNT.labels(reason="mandatory_strict_abort").inc()
            degrade.append("mandatory_strict_abort")
            state["degrade_reasons"] = degrade
            return state

        ANALYSIS_AGENT_DEGRADE_COUNT.labels(reason="mandatory_empty_continue").inc()
        if "mandatory_empty_continue" not in degrade:
            degrade.append("mandatory_empty_continue")
        for iid in failed_mandatory:
            tag = f"mandatory_degraded:{iid}"
            if tag not in degrade:
                degrade.append(tag)
        state["degrade_reasons"] = degrade
        return state

    def run_slot_quality(self, state: AnalysisAgentState) -> AnalysisAgentState:
        """兼容旧名：转发到全量 data_quality（忽略按章 HITL）。"""
        return self.run_data_quality(state)

    def apply_human_response(self, state: AnalysisAgentState, response: dict[str, Any]) -> AnalysisAgentState:
        """兼容 resume API；T1 主路径不再进入 HITL。"""
        action = str(response.get("action") or "skip_slot")
        payload = response.get("payload") or {}
        gathered = state.setdefault("gathered_data", {})
        task_status = state.setdefault("task_status", {})

        if action == "abort":
            state["abort_requested"] = True
            state["error"] = str(payload.get("reason") or "user aborted")
            return state

        if action in ("widen_time_range", "retry"):
            if action == "widen_time_range":
                tr = payload.get("time_range")
                if isinstance(tr, dict):
                    opts = state.setdefault("options", {})
                    opts["time_range"] = tr
                    q = state.get("query") or ""
                    state["query"] = f"{q}（时间范围已调整）"
            for iid in list(task_status.keys()):
                gathered.pop(iid, None)
                task_status.pop(iid, None)
            state["acquire_retry"] = True
            state["slot_retry_nl2sql"] = True
            return state

        if action == "skip_slot":
            state["slot_skipped"] = True
            return state

        return state

    async def run_slot_synthesize(self, state: AnalysisAgentState) -> AnalysisAgentState:
        if state.get("abort_requested"):
            state["_last_slot_output"] = {
                "slot_id": "",
                "kind": "static_markdown",
                "title": "",
                "markdown": "",
                "table": None,
                "chart": None,
                "charts": [],
                "error": state.get("error"),
            }
            state["_last_stream_chunks"] = []
            state["_narrative_live_streamed"] = False
            return state

        if state.get("slot_skipped"):
            state["slot_skipped"] = False
            state["_last_slot_output"] = asdict(
                SlotOutput(self.current_slot(state).id, self.current_slot(state).kind, "", "（本槽已跳过）\n\n")
            )
            state["_last_stream_chunks"] = []
            state["_narrative_live_streamed"] = False
            return state

        if await self._is_cancelled(state):
            state["abort_requested"] = True
            state["error"] = "user_cancelled"
            state["_narrative_live_streamed"] = False
            return state

        slot = self.current_slot(state)
        out, chunks, live = await self._synthesize_slot(state, slot)
        # 合并 chapter_prepare 配置化表/图
        prepared = state.get("_prepared_viz") or {}
        prep_tables = list(prepared.get("tables") or [])
        prep_charts = list(prepared.get("charts") or [])
        prep_mds = list(prepared.get("table_markdowns") or [])
        if prep_mds:
            extra_md = "\n\n".join(m for m in prep_mds if m)
            if extra_md.strip():
                out.markdown = (out.markdown or "") + ("\n\n" if out.markdown else "") + extra_md
        if prep_tables and not out.table:
            out.table = prep_tables[0]
        elif prep_tables:
            # 主 table 已有时仍把配置表并入 charts 旁路：全部进 emit 列表
            pass
        merged_charts = list(out.charts or [])
        seen_ids = {str(c.get("id")) for c in merged_charts if isinstance(c, dict)}
        for ch in prep_charts:
            cid = str(ch.get("id") or "")
            if cid and cid not in seen_ids:
                merged_charts.append(ch)
                seen_ids.add(cid)
        out.charts = merged_charts
        if out.chart is None and merged_charts:
            out.chart = merged_charts[0]

        state["_last_slot_output"] = {
            "slot_id": out.slot_id,
            "kind": out.kind,
            "title": out.title,
            "markdown": out.markdown,
            "table": out.table,
            "chart": out.chart,
            "charts": out.charts,
            "error": out.error,
            "prepared_tables": prep_tables,
        }
        state["_last_stream_chunks"] = chunks
        state["_narrative_live_streamed"] = live
        if await self._is_cancelled(state):
            state["abort_requested"] = True
            state["error"] = "user_cancelled"
        return state

    def run_slot_prepare(self, state: AnalysisAgentState) -> AnalysisAgentState:
        """T4：按报告 tables[]/charts[] 为当前章程序渲染表/图。"""
        if state.get("abort_requested") or state.get("slot_skipped"):
            state["_prepared_viz"] = {
                "tables": [],
                "charts": [],
                "table_markdowns": [],
                "note": "",
            }
            return state
        slot = self.current_slot(state)
        options = state.get("options") or {}
        chart_mode = str(options.get("chart_mode") or "auto")
        prepared = prepare_chapter_viz(
            chapter_id=slot.id,
            report_tables=list(state.get("report_tables") or []),
            report_charts=list(state.get("report_charts") or []),
            gathered_data=state.get("gathered_data") or {},
            chart_mode=chart_mode,
        )
        state["_prepared_viz"] = prepared
        # 提前推送表/图 SSE（先于叙述 token）
        events: list[dict[str, Any]] = []
        if self._cfg.enable_structured_sse_events:
            for tbl in prepared.get("tables") or []:
                events.append(
                    {
                        "event": "analysis_agent_table_payload",
                        "table": tbl,
                        "slot_id": slot.id,
                        "configured": True,
                    }
                )
            for ch in prepared.get("charts") or []:
                events.append(
                    {
                        "event": "analysis_agent_chart_payload",
                        "chart": ch,
                        "slot_id": slot.id,
                        "configured": True,
                    }
                )
        if events:
            _append_events(state, events)
        return state

    def run_slot_emit(self, state: AnalysisAgentState) -> AnalysisAgentState:
        if state.get("abort_requested"):
            state["slot_index"] = int(state.get("slots_total") or 0)
            return state

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

    # 方案命名：chapter_*（与 LangGraph 节点一致）
    def run_chapter_prepare(self, state: AnalysisAgentState) -> AnalysisAgentState:
        return self.run_slot_prepare(state)

    async def run_chapter_synthesize(self, state: AnalysisAgentState) -> AnalysisAgentState:
        return await self.run_slot_synthesize(state)

    def run_chapter_emit(self, state: AnalysisAgentState) -> AnalysisAgentState:
        return self.run_slot_emit(state)

    def build_final_result(self, state: AnalysisAgentState) -> dict[str, Any]:
        summary = "".join(state.get("summary_parts") or [])
        nl2sql_calls = state.get("nl2sql_calls") or []
        cache_hits = sum(1 for c in nl2sql_calls if c.get("cache_hit"))
        degrade = list(state.get("degrade_reasons") or [])
        err = state.get("error")
        if state.get("abort_requested") and err == "user_cancelled":
            status = "aborted"
        elif state.get("abort_requested") and err:
            status = "failed"
        else:
            status = "success"
        result: dict[str, Any] = {
            "request_id": state.get("request_id"),
            "analysis_type": state.get("analysis_type"),
            "user_id": state.get("user_id"),
            "session_id": state.get("session_id"),
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
                "degrade_reasons": degrade,
            },
            "degrade_reasons": degrade,
            "quality_l1": state.get("quality_l1") or {},
            "slot_trace": state.get("slot_trace") or [],
            "trace": {
                **(state.get("trace") or {}),
                "slot_trace": state.get("slot_trace") or [],
                "from_report_spec": bool(state.get("from_report_spec")),
                "orchestrator": "langgraph_acquire_then_chapters",
                "checkpoint_thread_id": state.get("_checkpoint_thread_id"),
                "status": status,
                "error": err,
                "degrade_reasons": degrade,
                "chapter_synth_max_parallel": self._resolve_chapter_synth_parallel(
                    state.get("options") or {}
                ),
            },
        }
        if status == "aborted":
            result["trace"]["terminate_reason"] = "user_cancelled"
            result["meta"] = {
                "status": "aborted",
                "terminate_reason": "user_cancelled",
                "is_partial": True,
                "stream_id": state.get("_stream_id"),
                "request_id": state.get("request_id"),
            }
        return result

    async def _run_one_plan_item(
        self,
        *,
        state: AnalysisAgentState,
        task: dict[str, Any],
        analysis_type: str,
        plan_version: str,
        max_rows: int,
        max_item_attempts: int,
    ) -> list[dict[str, Any]]:
        """执行单个 plan item；返回本项相关 SSE 事件。"""
        events: list[dict[str, Any]] = []
        gathered = state.setdefault("gathered_data", {})
        task_status = state.setdefault("task_status", {})
        nl2sql_calls = state.setdefault("nl2sql_calls", [])
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
                }
            )
            events.append(
                {
                    "event": "analysis_agent_nl2sql_done",
                    "item_id": item_id,
                    "row_count": len(rows),
                    "latency_ms": 0,
                    "cached": True,
                }
            )
            return events

        question = str(task.get("question") or "")
        mandatory = bool(task.get("mandatory", False))
        last_err: str | None = None
        for attempt in range(max_item_attempts):
            try:
                rows, call_rec = await run_nl2sql_for_plan_item(
                    nl2sql=self._nl2sql,
                    user_id=state["user_id"],
                    session_id=state["session_id"],
                    question=question,
                    item_id=item_id,
                    analysis_type=analysis_type,
                    plan_template_version=plan_version,
                    analysis_request_id=state["request_id"],
                    query=str(state.get("query") or ""),
                    disable_qa_slot_replay=self._resolve_disable_qa_slot_replay(state),
                )
                rows = rows[:max_rows]
                call_rec["attempt"] = attempt
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
                        "cached": False,
                    }
                )
                return events
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
        events.append(
            {
                "event": "analysis_agent_nl2sql_done",
                "item_id": item_id,
                "row_count": 0,
                "latency_ms": 0,
                "cached": False,
                "error": last_err,
            }
        )
        return events

    async def _acquire_all_plan_data(
        self,
        state: AnalysisAgentState,
        plan_tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """dependency 分层 + 同层 asyncio.gather 并行取数。"""
        if not plan_tasks:
            return []

        options = state.get("options") or {}
        max_rows = int(options.get("max_rows_per_query") or 2000)
        analysis_type = str(state.get("analysis_type") or "overheat_guidance")
        merged_opts = dict(options)
        trace_ptv = (state.get("trace") or {}).get("plan_template_version")
        if trace_ptv:
            merged_opts["plan_template_version"] = trace_ptv
        plan_version = effective_plan_version(analysis_type, merged_opts)
        max_par = max(1, int(self._cfg.acquire_max_parallel))
        # 单项内 attempt：外层 data_quality 负责整批重试；此处仅 1 次（避免双重放大）
        max_item_attempts = 1
        sem = asyncio.Semaphore(max_par)
        gathered = state.setdefault("gathered_data", {})
        task_status = state.setdefault("task_status", {})
        all_events: list[dict[str, Any]] = []
        unfinished = {str(t["item_id"]) for t in plan_tasks}
        task_by_id = {str(t["item_id"]): t for t in plan_tasks}

        async def bounded(task: dict[str, Any]) -> list[dict[str, Any]]:
            async with sem:
                return await self._run_one_plan_item(
                    state=state,
                    task=task,
                    analysis_type=analysis_type,
                    plan_version=plan_version,
                    max_rows=max_rows,
                    max_item_attempts=max_item_attempts,
                )

        while unfinished:
            if await self._is_cancelled(state):
                state["abort_requested"] = True
                state["error"] = "user_cancelled"
                all_events.append(
                    {
                        "event": "analysis_agent_cancelled",
                        "request_id": state.get("request_id"),
                        "terminate_reason": "user_cancelled",
                        "phase": "acquire_data",
                    }
                )
                break
            runnable: list[dict[str, Any]] = []
            for iid in list(unfinished):
                task = task_by_id[iid]
                deps = self._dependency_ids(task)
                if any(d in unfinished for d in deps):
                    continue
                if deps and not all(task_status.get(d) == "success" for d in deps):
                    # 依赖未成功：标记 skipped，不再查询
                    gathered[iid] = []
                    mandatory = bool(task.get("mandatory", False))
                    task_status[iid] = (
                        "mandatory_failed" if mandatory else "optional_failed"
                    )
                    state.setdefault("nl2sql_calls", []).append(
                        {
                            "item_id": iid,
                            "row_count": 0,
                            "latency_ms": 0,
                            "analysis_type": analysis_type,
                            "plan_template_version": plan_version,
                            "cache_hit": False,
                            "status": "skipped_dependency",
                        }
                    )
                    all_events.append(
                        {
                            "event": "analysis_agent_nl2sql_done",
                            "item_id": iid,
                            "row_count": 0,
                            "latency_ms": 0,
                            "cached": False,
                            "skipped": True,
                            "reason": "dependency_not_success",
                        }
                    )
                    unfinished.discard(iid)
                    continue
                if plan_item_resolved(iid, gathered_data=gathered, task_status=task_status):
                    rows = list(gathered.get(iid) or [])
                    state.setdefault("nl2sql_calls", []).append(
                        {
                            "item_id": iid,
                            "row_count": len(rows),
                            "latency_ms": 0,
                            "analysis_type": analysis_type,
                            "plan_template_version": plan_version,
                            "cache_hit": True,
                        }
                    )
                    all_events.append(
                        {
                            "event": "analysis_agent_nl2sql_done",
                            "item_id": iid,
                            "row_count": len(rows),
                            "latency_ms": 0,
                            "cached": True,
                        }
                    )
                    unfinished.discard(iid)
                    continue
                runnable.append(task)

            if not runnable:
                # 死锁兜底：剩余全部标失败
                for iid in list(unfinished):
                    task = task_by_id[iid]
                    gathered[iid] = []
                    mandatory = bool(task.get("mandatory", False))
                    task_status[iid] = (
                        "mandatory_failed" if mandatory else "optional_failed"
                    )
                    unfinished.discard(iid)
                break

            logger.info(
                "analysis_agent acquire_data wave item_ids=%s unfinished=%s",
                [t["item_id"] for t in runnable],
                len(unfinished),
            )
            wave_events = await asyncio.gather(*[bounded(t) for t in runnable])
            for evs, task in zip(wave_events, runnable):
                all_events.extend(evs)
                unfinished.discard(str(task["item_id"]))

        return all_events

    async def _acquire_slot_data(
        self,
        state: AnalysisAgentState,
        slot: AnalysisAgentSlot,
        plan_tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """章级取数（兼容测试）；优先命中全量缓存。"""
        events: list[dict[str, Any]] = []
        options = state.get("options") or {}
        max_rows = int(options.get("max_rows_per_query") or 2000)
        gathered = state.setdefault("gathered_data", {})
        task_status = state.setdefault("task_status", {})
        slot_plan = plan_tasks_for_slot(plan_tasks, slot.source_item_ids)
        if not slot_plan:
            return events

        analysis_type = str(state.get("analysis_type") or "overheat_guidance")
        merged_opts = dict(options)
        trace_ptv = (state.get("trace") or {}).get("plan_template_version")
        if trace_ptv:
            merged_opts["plan_template_version"] = trace_ptv
        plan_version = effective_plan_version(analysis_type, merged_opts)

        async def _one(task: dict[str, Any]) -> None:
            item_id = str(task["item_id"])
            if plan_item_resolved(item_id, gathered_data=gathered, task_status=task_status):
                rows = list(gathered.get(item_id) or [])
                state.setdefault("nl2sql_calls", []).append(
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
            evs = await self._run_one_plan_item(
                state=state,
                task=task,
                analysis_type=analysis_type,
                plan_version=plan_version,
                max_rows=max_rows,
                max_item_attempts=max(1, int(slot.max_nl2sql_retries or 0) + 1),
            )
            for ev in evs:
                ev["slot_id"] = slot.id
            events.extend(evs)

        await asyncio.gather(*[_one(t) for t in slot_plan])
        return events

    async def _is_cancelled(self, state: AnalysisAgentState) -> bool:
        checker = state.get("_cancel_checker")
        if checker is None:
            return False
        try:
            return bool(await checker())
        except Exception:  # noqa: BLE001
            return False

    def _resolve_disable_qa_slot_replay(self, state: AnalysisAgentState) -> bool:
        opts = state.get("options") or {}
        if "disable_qa_slot_replay" in opts and opts.get("disable_qa_slot_replay") is not None:
            return bool(opts.get("disable_qa_slot_replay"))
        return bool(self._cfg.nl2sql_disable_qa_slot_replay)

    def _resolve_use_react(self, options: dict[str, Any]) -> bool:
        if "use_react_agent" in options and options.get("use_react_agent") is not None:
            return bool(options.get("use_react_agent"))
        return bool(self._cfg.use_react_agent)

    def _try_get_stream_writer(self) -> Any | None:
        try:
            from langgraph.config import get_stream_writer  # type: ignore[import-not-found]

            return get_stream_writer()
        except Exception:  # noqa: BLE001
            return None

    async def _synthesize_slot(
        self,
        state: AnalysisAgentState,
        slot: AnalysisAgentSlot,
    ) -> tuple[SlotOutput, list[str], bool]:
        """返回 (output, chunks, live_streamed)。live_streamed 时 chunks 为空且正文已推送。"""
        options = state.get("options") or {}
        chart_mode = str(options.get("chart_mode") or "auto")
        gathered = state.get("gathered_data") or {}
        task_status = state.get("task_status") or {}
        context_snippets = list(state.get("intent_context") or state.get("context_snippets") or [])
        query = state.get("query") or ""
        analysis_type = str(state.get("analysis_type") or "overheat_guidance")
        t_slot = time.perf_counter()
        narrative_streaming = options.get("narrative_streaming", self._cfg.narrative_streaming)
        if narrative_streaming is None:
            narrative_streaming = self._cfg.narrative_streaming
        # stream_live 与真流式绑定：全局开 或 本章显式 stream_live
        narrative_streaming = bool(narrative_streaming) or bool(slot.stream_live)
        # 章合同并行时强制缓冲，避免 SSE delta 乱序
        if options.get("_force_buffered_synth"):
            narrative_streaming = False

        if slot.kind in ("llm_narrative", "llm_section"):
            use_react = self._resolve_use_react(options)
            writer = self._try_get_stream_writer() if narrative_streaming else None
            live = bool(narrative_streaming)
            slot_index = int(state.get("slot_index") or 0)

            def _push_live(ev: dict[str, Any]) -> None:
                if writer is not None:
                    try:
                        writer(ev)
                        return
                    except Exception:  # noqa: BLE001
                        pass
                _append_events(state, [ev])

            async def on_delta(text: str) -> None:
                if not text:
                    return
                _push_live(
                    {
                        "event": "analysis_agent_summary_delta",
                        "text": text,
                        "slot_id": slot.id,
                    }
                )

            _push_live(
                {
                    "event": "analysis_agent_chapter_start",
                    "slot_id": slot.id,
                    "chapter_id": slot.id,
                    "slot_index": slot_index,
                    "chapter_index": slot_index,
                    "kind": slot.kind,
                }
            )
            if live and slot.title.strip():
                await on_delta(f"### {slot.title.strip()}\n\n")

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
                on_delta=on_delta if live else None,
                cancel_checker=state.get("_cancel_checker"),
                prepared_viz_note=str((state.get("_prepared_viz") or {}).get("note") or ""),
            )
            out, chunks = section_result_to_slot_output(
                slot, result, already_streamed=live
            )
            ANALYSIS_AGENT_SLOT_LATENCY.labels(
                slot_kind=slot.kind, analysis_type=analysis_type
            ).observe(time.perf_counter() - t_slot)
            return out, chunks, live

        out = render_deterministic_slot(
            slot=slot,
            gathered_data=gathered,
            task_status=task_status,
            chart_mode=chart_mode,
        )
        ANALYSIS_AGENT_SLOT_LATENCY.labels(
            slot_kind=slot.kind, analysis_type=analysis_type
        ).observe(time.perf_counter() - t_slot)
        return out, _chunk_text(out.markdown, self._cfg.stream_chunk_chars), False

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
        live = bool(state.get("_narrative_live_streamed"))

        # 真流式已在 synthesize 推送 chapter_start
        if not live:
            events.append(
                {
                    "event": "analysis_agent_chapter_start",
                    "slot_id": slot.id,
                    "chapter_id": slot.id,
                    "slot_index": slot_index,
                    "chapter_index": slot_index,
                    "kind": slot.kind,
                }
            )

        # 真流式正文已在 synthesize 阶段推送；此处避免整章假切片重复
        if not live:
            if stream_chunks and slot.kind in ("llm_narrative", "llm_section"):
                for piece in stream_chunks:
                    events.append({"event": "analysis_agent_summary_delta", "text": piece})
            else:
                for piece in _chunk_text(out.markdown, self._cfg.stream_chunk_chars):
                    events.append({"event": "analysis_agent_summary_delta", "text": piece})

        if out.table and self._cfg.enable_structured_sse_events:
            prepared_ids = {
                str(t.get("id"))
                for t in ((state.get("_prepared_viz") or {}).get("tables") or [])
                if isinstance(t, dict)
            }
            if str(out.table.get("id") or "") not in prepared_ids:
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

        prepared_chart_ids = {
            str(c.get("id"))
            for c in ((state.get("_prepared_viz") or {}).get("charts") or [])
            if isinstance(c, dict)
        }
        for ch in out.charts:
            if str(ch.get("id") or "") in prepared_chart_ids:
                continue
            events.append(
                {"event": "analysis_agent_chart_payload", "chart": ch, "slot_id": slot.id}
            )

        state.setdefault("summary_parts", []).append(out.markdown)
        sections = state.setdefault("structured_report", {}).setdefault("sections", [])
        if out.title and out.markdown.strip():
            sections.append(
                {"title": out.title, "content": out.markdown.strip(), "slot_id": slot.id}
            )
        # 配置化表优先全部入库
        sr_tables = state.setdefault("structured_report", {}).setdefault("tables", [])
        for tbl in (state.get("_prepared_viz") or {}).get("tables") or []:
            if isinstance(tbl, dict):
                sr_tables.append(tbl)
        if out.table and str(out.table.get("id") or "") not in {
            str(t.get("id")) for t in sr_tables if isinstance(t, dict)
        }:
            sr_tables.append(out.table)
        sr_charts = state.setdefault("structured_report", {}).setdefault("charts", [])
        for ch in (state.get("_prepared_viz") or {}).get("charts") or []:
            if isinstance(ch, dict):
                sr_charts.append(ch)
        for ch in out.charts:
            cid = str(ch.get("id") or "")
            if cid and cid not in {str(c.get("id")) for c in sr_charts if isinstance(c, dict)}:
                sr_charts.append(ch)

        state.setdefault("slot_trace", []).append(
            {
                "slot_id": slot.id,
                "kind": slot.kind,
                "chars": len(out.markdown),
                "error": out.error,
                "live_streamed": live,
            }
        )
        events.append(
            {
                "event": "analysis_agent_chapter_complete",
                "slot_id": slot.id,
                "chapter_id": slot.id,
            }
        )
        return events
