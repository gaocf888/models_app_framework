from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.analysis_agent.slots.builder import slot_from_dict as slot_from_spec_dict
from app.analysis_agent.slots.kinds import AnalysisAgentSlot


def slot_to_dict(slot: AnalysisAgentSlot) -> dict[str, Any]:
    return asdict(slot)


def slot_from_dict(data: dict[str, Any]) -> AnalysisAgentSlot:
    """还原 initialize 写入 state 的章节，须保留 outline/constraints 等蓝图字段。"""
    return slot_from_spec_dict(data)
