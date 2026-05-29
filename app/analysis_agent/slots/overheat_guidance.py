"""超温分析（overheat_guidance）槽位 — 从配置加载（统一多槽位版本 v1）。

配置源：
  - configs/analysis_agent_slots/overheat_guidance.v1.json
  - prompts.yaml · analysis_agent_slots_overheat_guidance · v1
"""

from __future__ import annotations

from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.slots.loader import load_agent_slots
from app.analysis_agent.slots.specs import DEFAULT_AGENT_TEMPLATE_VERSION


def overheat_guidance_slots() -> list[AnalysisAgentSlot]:
    return load_agent_slots("overheat_guidance", version=DEFAULT_AGENT_TEMPLATE_VERSION)
