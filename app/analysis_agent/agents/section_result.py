from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SectionSynthesisResult:
    """llm_section / ReAct 槽合成结果（正文 + 工具产出的表/图）。"""

    markdown: str
    tables: list[dict[str, Any]] = field(default_factory=list)
    charts: list[dict[str, Any]] = field(default_factory=list)
    table_markdowns: list[str] = field(default_factory=list)
