from __future__ import annotations

import time
from typing import Any

from app.analysis_agent.context_loader import load_analysis_run_context
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.plans.loader import effective_plan_version
from app.analysis_agent.slots.serialize import slot_to_dict
from app.core.logging import get_logger

logger = get_logger(__name__)


def make_nodes(orchestrator: SlotOrchestrator) -> dict[str, Any]:
    async def initialize(state: AnalysisAgentState) -> AnalysisAgentState:
        analysis_type = state["analysis_type"]
        opts = state.get("options") or {}
        plan_version = effective_plan_version(analysis_type, opts)
        ctx = load_analysis_run_context(
            analysis_type, version=plan_version, prompts=orchestrator._prompts
        )
        plan_version = ctx.plan_template_version
        opts["plan_template_version"] = plan_version
        slots = ctx.slots
        state["ordered_slots"] = [slot_to_dict(s) for s in slots]
        state["slots_total"] = len(slots)
        state["slot_index"] = 0
        state["plan_tasks"] = ctx.plan_tasks
        if ctx.report_title:
            state["report_title"] = ctx.report_title
        state["from_report_spec"] = ctx.from_report_spec
        state.setdefault("gathered_data", {})
        state.setdefault("task_status", {})
        state.setdefault("nl2sql_calls", [])
        state.setdefault("summary_parts", [])
        state.setdefault("slot_trace", [])
        state.setdefault("human_interactions", [])
        state.setdefault("degrade_reasons", [])
        state.setdefault("_acquire_retries", 0)
        state["report_tables"] = list(ctx.report_tables or [])
        state["report_charts"] = list(ctx.report_charts or [])
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
                "slot_total": len(slots),
                "plan_items": len(state["plan_tasks"]),
                "from_report_spec": ctx.from_report_spec,
                "plan_template_version": plan_version,
            }
        ]
        return state

    def intent_rag(state: AnalysisAgentState) -> AnalysisAgentState:
        return orchestrator.run_intent_rag(state)

    async def acquire_data(state: AnalysisAgentState) -> AnalysisAgentState:
        return await orchestrator.run_acquire_data(state)

    def data_quality(state: AnalysisAgentState) -> AnalysisAgentState:
        return orchestrator.run_data_quality(state)

    async def chapter_pipeline(state: AnalysisAgentState) -> AnalysisAgentState:
        return await orchestrator.run_chapter_pipeline(state)

    def finalize(state: AnalysisAgentState) -> AnalysisAgentState:
        from datetime import datetime, timezone

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
        return state

    return {
        "initialize": initialize,
        "intent_rag": intent_rag,
        "acquire_data": acquire_data,
        "data_quality": data_quality,
        "chapter_pipeline": chapter_pipeline,
        "finalize": finalize,
    }
