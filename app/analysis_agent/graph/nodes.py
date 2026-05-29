from __future__ import annotations

import time
from typing import Any

from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.context_loader import load_analysis_run_context
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
        state.setdefault(
            "structured_report",
            {"sections": [], "tables": [], "charts": [], "suggestions": []},
        )
        state.setdefault("pending_events", [])
        state["trace"] = {
            **(state.get("trace") or {}),
            "module": "analysis_agent",
            "orchestrator": "langgraph_slot_pipeline",
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

    async def slot_nl2sql(state: AnalysisAgentState) -> AnalysisAgentState:
        return await orchestrator.run_slot_nl2sql(state)

    def slot_quality(state: AnalysisAgentState) -> AnalysisAgentState:
        return orchestrator.run_slot_quality(state)

    async def slot_human(state: AnalysisAgentState) -> AnalysisAgentState:
        from langgraph.types import interrupt  # type: ignore[import-not-found]

        payload = {
            "prompt": state.get("human_prompt") or "需要您的确认以继续分析",
            "suggested_actions": state.get("human_suggested_actions")
            or ["retry", "skip_slot", "abort"],
            "slot_id": orchestrator.current_slot(state).id,
            "request_id": state.get("request_id"),
        }
        human = interrupt(payload)
        if not isinstance(human, dict):
            human = {"action": "skip_slot", "payload": {}}
        state.setdefault("human_interactions", []).append(
            {"slot_id": payload["slot_id"], "request": payload, "response": human}
        )
        orchestrator.apply_human_response(state, human)
        state["needs_human_interrupt"] = False
        state["pending_events"] = [
            {
                "event": "analysis_agent_human_resumed",
                "slot_id": payload["slot_id"],
                "action": human.get("action"),
            }
        ]
        return state

    async def slot_synthesize(state: AnalysisAgentState) -> AnalysisAgentState:
        return await orchestrator.run_slot_synthesize(state)

    def slot_emit(state: AnalysisAgentState) -> AnalysisAgentState:
        return orchestrator.run_slot_emit(state)

    def finalize(state: AnalysisAgentState) -> AnalysisAgentState:
        started = float(state.get("_run_started_at") or time.perf_counter())
        result = orchestrator.build_final_result(state)
        result["trace"]["total_ms"] = int((time.perf_counter() - started) * 1000)
        state["_final_result"] = result
        state["pending_events"] = [
            {
                "event": "analysis_agent_report_complete",
                "request_id": state.get("request_id"),
                "summary_length": len(result.get("summary") or ""),
                "structured_report": result.get("structured_report"),
            },
            {
                "event": "analysis_agent_finished",
                "request_id": state.get("request_id"),
                "result": result,
            },
        ]
        return state

    return {
        "initialize": initialize,
        "intent_rag": intent_rag,
        "slot_nl2sql": slot_nl2sql,
        "slot_quality": slot_quality,
        "slot_human": slot_human,
        "slot_synthesize": slot_synthesize,
        "slot_emit": slot_emit,
        "finalize": finalize,
    }
