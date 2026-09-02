"""P0 使用 sequential runner；此处保留节点清单以便后续接 LangGraph。"""

from __future__ import annotations

from app.data_query_agent.graph.nodes import NODE_ORDER


def planned_node_order() -> tuple[str, ...]:
    return NODE_ORDER
