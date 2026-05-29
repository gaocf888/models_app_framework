"""LangGraph 多节点槽位流水线（含 interrupt / checkpoint）。"""

from __future__ import annotations

from typing import Any, Literal

from app.analysis_agent.checkpoint import build_analysis_agent_checkpointer
from app.analysis_agent.graph.nodes import make_nodes
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.core.logging import get_logger

logger = get_logger(__name__)


def _route_after_quality(state: AnalysisAgentState) -> Literal["slot_nl2sql", "slot_human", "slot_synthesize", "finalize"]:
    if state.get("abort_requested"):
        return "finalize"
    if state.get("slot_retry_nl2sql"):
        return "slot_nl2sql"
    if state.get("needs_human_interrupt"):
        return "slot_human"
    return "slot_synthesize"


def _route_slot_loop(state: AnalysisAgentState) -> Literal["slot_nl2sql", "finalize"]:
    idx = int(state.get("slot_index") or 0)
    total = int(state.get("slots_total") or 0)
    if state.get("abort_requested") or idx >= total:
        return "finalize"
    return "slot_nl2sql"


def build_analysis_agent_graph(orchestrator: SlotOrchestrator) -> tuple[Any | None, Any | None]:
    """
    编译 LangGraph；返回 (compiled_graph, checkpointer)。
    不可用时 (None, None)。
    """
    try:
        from langgraph.graph import END, StateGraph  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("langgraph not available; analysis_agent sequential fallback only")
        return None, None

    nodes = make_nodes(orchestrator)
    g = StateGraph(AnalysisAgentState)
    g.add_node("initialize", nodes["initialize"])
    g.add_node("intent_rag", nodes["intent_rag"])
    g.add_node("slot_nl2sql", nodes["slot_nl2sql"])
    g.add_node("slot_quality", nodes["slot_quality"])
    g.add_node("slot_human", nodes["slot_human"])
    g.add_node("slot_synthesize", nodes["slot_synthesize"])
    g.add_node("slot_emit", nodes["slot_emit"])
    g.add_node("finalize", nodes["finalize"])

    g.set_entry_point("initialize")
    g.add_edge("initialize", "intent_rag")
    g.add_edge("intent_rag", "slot_nl2sql")
    g.add_edge("slot_nl2sql", "slot_quality")
    g.add_conditional_edges("slot_quality", _route_after_quality)
    g.add_edge("slot_human", "slot_synthesize")
    g.add_edge("slot_synthesize", "slot_emit")
    g.add_conditional_edges("slot_emit", _route_slot_loop)
    g.add_edge("finalize", END)

    checkpointer = build_analysis_agent_checkpointer()
    compiled = g.compile(checkpointer=checkpointer)
    return compiled, checkpointer
