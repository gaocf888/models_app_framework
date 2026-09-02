"""节点顺序：initialize → library_intent → (HITL) → scope_intent → acquire_data → assemble。"""

from __future__ import annotations

from typing import Any

# Sequential runner 实现于 runner.py；本模块仅列出节点名，供后续接 LangGraph。
NODE_ORDER = (
    "initialize",
    "library_intent",
    "library_hitl",
    "scope_intent",
    "acquire_data",
    "assemble_result",
    "finalize",
)


def node_names() -> tuple[str, ...]:
    return NODE_ORDER
