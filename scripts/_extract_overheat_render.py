"""One-off extract overheat render helpers for analysis_agent."""
from pathlib import Path

src_path = Path("app/llm/graphs/analysis_synthesis_v2.py")
lines = src_path.read_text(encoding="utf-8").splitlines()
chunk = lines[391:1406]
header = '''"""Overheat deterministic render helpers (forked for analysis_agent)."""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal

from app.analysis_agent.slots.kinds import AnalysisAgentSlot, SlotOutput
from app.core.logging import get_logger

logger = get_logger(__name__)

'''
body = "\n".join(chunk)
body = body.replace("SynthesisV2Slot", "AnalysisAgentSlot")
body = body.replace("SynthesisV2SlotOutput", "SlotOutput")
# 已废弃：确定性渲染已移除，请维护 configs/analysis_agent_reports/*.json
out = Path("app/analysis_agent/renderers/overheat_core.py")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(header + body, encoding="utf-8")
print("wrote", out, "lines", len(chunk))
