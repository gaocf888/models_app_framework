"""ReAct 工具运行时上下文（线程局部，供 narrative agent 工具读取当前槽状态）。"""

from __future__ import annotations

import contextvars
from typing import Any

_slot_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "analysis_agent_slot_ctx", default=None
)


def set_slot_tool_context(ctx: dict[str, Any]) -> contextvars.Token:
    return _slot_ctx.set(ctx)


def reset_slot_tool_context(token: contextvars.Token) -> None:
    _slot_ctx.reset(token)


def get_slot_tool_context() -> dict[str, Any]:
    return _slot_ctx.get() or {}
