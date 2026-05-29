from __future__ import annotations

from typing import Any

from app.analysis_agent.slots.kinds import AnalysisAgentSlot, SlotOutput
from app.core.logging import get_logger

logger = get_logger(__name__)

_DEPRECATED_KINDS = frozenset(
    {"template_deterministic", "table_deterministic", "chart_structured", "llm_narrative"}
)


def render_deterministic_slot(
    *,
    slot: AnalysisAgentSlot,
    gathered_data: dict[str, list[dict]],
    task_status: dict[str, str] | None,
    chart_mode: str,
) -> SlotOutput:
    """仅支持 static_markdown；其余确定性槽类型已废弃（由 llm_section + emit 工具替代）。"""
    _ = gathered_data, task_status, chart_mode
    title = slot.title.strip()
    if slot.kind == "static_markdown":
        return SlotOutput(slot.id, slot.kind, title, slot.static_body)

    if slot.kind in _DEPRECATED_KINDS:
        logger.warning(
            "analysis_agent deprecated slot kind=%s slot_id=%s; use llm_section in report spec",
            slot.kind,
            slot.id,
        )
        md = f"### {title}\n\n（本章应使用 Agent 章节渲染；槽位类型 `{slot.kind}` 已废弃。）\n\n"
        return SlotOutput(slot.id, slot.kind, title, md, error=f"deprecated_kind:{slot.kind}")

    return SlotOutput(slot.id, slot.kind, title, "", error=f"unknown_kind:{slot.kind}")
