"""LangGraph：全量 acquire_data → 质量门 → chapter_pipeline → finalize。"""

from __future__ import annotations

from typing import Any, Literal

from app.analysis_agent.checkpoint import build_analysis_agent_checkpointer
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.core.logging import get_logger

logger = get_logger(__name__)


def _route_after_quality(
    state: AnalysisAgentState,
) -> Literal["acquire_data", "chapter_pipeline", "finalize"]:
    if state.get("abort_requested"):
        return "finalize"
    if state.get("acquire_retry") or state.get("slot_retry_nl2sql"):
        return "acquire_data"
    return "chapter_pipeline"


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

    # 惰性导入，避免 graph → nodes → context_loader ↔ slots 循环依赖
    from app.analysis_agent.graph.nodes import make_nodes

    nodes = make_nodes(orchestrator)
    g = StateGraph(AnalysisAgentState)
    g.add_node("initialize", nodes["initialize"])
    g.add_node("intent_rag", nodes["intent_rag"])
    g.add_node("acquire_data", nodes["acquire_data"])
    g.add_node("data_quality", nodes["data_quality"])
    g.add_node("chapter_pipeline", nodes["chapter_pipeline"])
    g.add_node("finalize", nodes["finalize"])

    g.set_entry_point("initialize")
    g.add_edge("initialize", "intent_rag")
    g.add_edge("intent_rag", "acquire_data")
    g.add_edge("acquire_data", "data_quality")
    g.add_conditional_edges("data_quality", _route_after_quality)
    g.add_edge("chapter_pipeline", "finalize")
    g.add_edge("finalize", END)

    checkpointer = build_analysis_agent_checkpointer()
    compiled = g.compile(checkpointer=checkpointer)
    return compiled, checkpointer
