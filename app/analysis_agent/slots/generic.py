"""由规格 dict 构建 AnalysisAgentSlot 列表（非超温专项）。"""

from __future__ import annotations

from app.analysis_agent.slots.builder import slot_from_dict
from app.analysis_agent.slots.kinds import AnalysisAgentSlot


def slots_from_specs(specs: list[dict]) -> list[AnalysisAgentSlot]:
    out: list[AnalysisAgentSlot] = []
    for s in specs:
        if isinstance(s, dict) and s.get("id"):
            out.append(slot_from_dict(s))
    return out
