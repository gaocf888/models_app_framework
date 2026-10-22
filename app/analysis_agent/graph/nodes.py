from __future__ import annotations

import time
from typing import Any

from app.analysis_agent.context_loader import load_analysis_run_context
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.period import (
    apply_resolved_period,
    canonical_query,
    fill_template_placeholders,
    fill_viz_placeholders,
    period_sse_dict,
)
from app.analysis_agent.period_probe import fallback_degrade_reasons
from app.analysis_agent.plans.loader import effective_plan_version
from app.analysis_agent.slots.serialize import slot_to_dict
from app.analysis_agent.slots.specs import is_subsidence_type
from app.core.logging import get_logger

logger = get_logger(__name__)

# LangGraph updates 会把节点返回里的 pending_events 再推一次。上一节点已下发的事件
# 不得原样带回，否则会出现多条 meta、多组 nl2sql_done。
_INIT_KEYS = (
    "query",
    "options",
    "ordered_slots",
    "slots_total",
    "slot_index",
    "plan_tasks",
    "report_title",
    "from_report_spec",
    "gathered_data",
    "task_status",
    "nl2sql_calls",
    "summary_parts",
    "slot_trace",
    "human_interactions",
    "degrade_reasons",
    "_acquire_retries",
    "report_tables",
    "report_charts",
    "structured_report",
    "trace",
    "intent_context",
    "_run_started_at",
)
_INTENT_KEYS = ("context_snippets", "intent_context")
_ACQUIRE_KEYS = (
    "plan_tasks",
    "gathered_data",
    "task_status",
    "nl2sql_calls",
    "acquire_retry",
    "slot_retry_nl2sql",
    "abort_requested",
    "error",
)
_QUALITY_KEYS = (
    "needs_human_interrupt",
    "acquire_retry",
    "slot_retry_nl2sql",
    "abort_requested",
    "error",
    "degrade_reasons",
    "_l1_anchor_checked",
    "quality_l1",
    "gathered_data",
    "task_status",
    "_acquire_retries",
)
_CHAPTER_KEYS = (
    "abort_requested",
    "error",
    "slot_index",
    "trace",
    "options",
    "_prepared_viz",
    "_last_slot_output",
    "_last_stream_chunks",
    "_narrative_live_streamed",
    "summary_parts",
    "structured_report",
    "slot_trace",
    "slot_outputs",
)
_FINALIZE_KEYS = ("_final_result",)


def _begin_node(state: AnalysisAgentState) -> None:
    """上一节点 updates 已把 pending_events 交给 runner 下发；本节点从空列表写增量。"""
    state["pending_events"] = []


def _increment(state: AnalysisAgentState, *keys: str) -> dict[str, Any]:
    """发出后只把本节点新增 SSE 与改过的字段写回图。"""
    events = list(state.pop("pending_events", []) or [])
    update: dict[str, Any] = {"pending_events": events}
    for k in keys:
        if k in state:
            update[k] = state[k]
    return update


def make_nodes(orchestrator: SlotOrchestrator) -> dict[str, Any]:
    async def initialize(state: AnalysisAgentState) -> dict[str, Any]:
        _begin_node(state)
        analysis_type = state["analysis_type"]
        opts = apply_resolved_period(analysis_type, state.get("options") or {})
        resolved = opts.get("_resolved") if isinstance(opts.get("_resolved"), dict) else {}
        if is_subsidence_type(analysis_type) and not str(state.get("query") or "").strip() and resolved:
            state["query"] = canonical_query(analysis_type, resolved)
        plan_version = effective_plan_version(analysis_type, opts)
        ctx = load_analysis_run_context(
            analysis_type, version=plan_version, prompts=orchestrator._prompts
        )
        plan_version = ctx.plan_template_version
        opts["plan_template_version"] = plan_version
        state["options"] = opts
        title = str(ctx.report_title or "")
        ordered: list[dict[str, Any]] = []
        for slot in ctx.slots:
            item = slot_to_dict(slot)
            if resolved:
                if item.get("static_body"):
                    item["static_body"] = fill_template_placeholders(
                        str(item["static_body"]), resolved, title=title
                    )
                if item.get("title"):
                    item["title"] = fill_template_placeholders(str(item["title"]), resolved, title=title)
            ordered.append(item)
        state["ordered_slots"] = ordered
        state["slots_total"] = len(ordered)
        state["slot_index"] = 0
        state["plan_tasks"] = ctx.plan_tasks
        if ctx.report_title:
            state["report_title"] = fill_template_placeholders(title, resolved, title=title) if resolved else title
        state["from_report_spec"] = ctx.from_report_spec
        state.setdefault("gathered_data", {})
        state.setdefault("task_status", {})
        state.setdefault("nl2sql_calls", [])
        state.setdefault("summary_parts", [])
        state.setdefault("slot_trace", [])
        state.setdefault("human_interactions", [])
        state.setdefault("degrade_reasons", [])
        for reason in fallback_degrade_reasons(resolved if isinstance(resolved, dict) else None):
            if reason not in state["degrade_reasons"]:
                state["degrade_reasons"].append(reason)
        state.setdefault("_acquire_retries", 0)
        tables = list(ctx.report_tables or [])
        charts = list(ctx.report_charts or [])
        if resolved:
            tables = fill_viz_placeholders(tables, resolved, title=title)
            charts = fill_viz_placeholders(charts, resolved, title=title)
        state["report_tables"] = tables
        state["report_charts"] = charts
        state.setdefault(
            "structured_report",
            {"sections": [], "tables": [], "charts": [], "suggestions": []},
        )
        state.setdefault("pending_events", [])
        state["trace"] = {
            **(state.get("trace") or {}),
            "module": "analysis_agent",
            "orchestrator": "langgraph_acquire_then_chapters",
            "plan_template_version": plan_version,
            "from_report_spec": ctx.from_report_spec,
        }
        state.setdefault("intent_context", [])
        state["_run_started_at"] = time.perf_counter()
        state["pending_events"] = [
            {
                "event": "analysis_agent_meta",
                "request_id": state["request_id"],
                "analysis_type": analysis_type,
                "slot_total": len(ordered),
                "plan_items": len(state["plan_tasks"]),
                "from_report_spec": ctx.from_report_spec,
                "plan_template_version": plan_version,
            }
        ]
        if resolved:
            state["pending_events"][0]["period"] = period_sse_dict(resolved)
        return _increment(state, *_INIT_KEYS)

    def intent_rag(state: AnalysisAgentState) -> dict[str, Any]:
        _begin_node(state)
        orchestrator.run_intent_rag(state)
        return _increment(state, *_INTENT_KEYS)

    async def acquire_data(state: AnalysisAgentState) -> dict[str, Any]:
        _begin_node(state)
        await orchestrator.run_acquire_data(state)
        return _increment(state, *_ACQUIRE_KEYS)

    def data_quality(state: AnalysisAgentState) -> dict[str, Any]:
        _begin_node(state)
        orchestrator.run_data_quality(state)
        return _increment(state, *_QUALITY_KEYS)

    async def chapter_pipeline(state: AnalysisAgentState) -> dict[str, Any]:
        _begin_node(state)
        await orchestrator.run_chapter_pipeline(state)
        return _increment(state, *_CHAPTER_KEYS)

    def finalize(state: AnalysisAgentState) -> dict[str, Any]:
        from datetime import datetime, timezone

        _begin_node(state)
        started = float(state.get("_run_started_at") or time.perf_counter())
        finished = time.perf_counter()
        latency_ms = int((finished - started) * 1000)
        result = orchestrator.build_final_result(state)
        result["trace"]["total_ms"] = latency_ms
        result["total_latency_ms"] = latency_ms
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        result.setdefault("finished_at", now_iso)
        result.setdefault("started_at", now_iso)
        state["_final_result"] = result
        events: list[dict[str, Any]] = []
        err = state.get("error")
        if state.get("abort_requested") and err == "user_cancelled":
            events.append(
                {
                    "event": "analysis_agent_cancelled",
                    "request_id": state.get("request_id"),
                    "terminate_reason": "user_cancelled",
                    "stream_id": state.get("_stream_id"),
                }
            )
        elif state.get("abort_requested") and err:
            events.append(
                {
                    "event": "analysis_agent_error",
                    "request_id": state.get("request_id"),
                    "message": err,
                }
            )
        events.extend(
            [
                {
                    "event": "analysis_agent_report_complete",
                    "request_id": state.get("request_id"),
                    "summary_length": len(result.get("summary") or ""),
                    "structured_report": result.get("structured_report"),
                    "degrade_reasons": result.get("degrade_reasons") or [],
                },
                {
                    "event": "analysis_agent_finished",
                    "request_id": state.get("request_id"),
                    "result": result,
                },
            ]
        )
        state["pending_events"] = events
        return _increment(state, *_FINALIZE_KEYS)

    return {
        "initialize": initialize,
        "intent_rag": intent_rag,
        "acquire_data": acquire_data,
        "data_quality": data_quality,
        "chapter_pipeline": chapter_pipeline,
        "finalize": finalize,
    }
