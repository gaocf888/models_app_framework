from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SlotKind = Literal[
    "llm_narrative",
    "llm_section",
    "table_deterministic",
    "chart_structured",
    "static_markdown",
    "template_deterministic",
]

TableKind = Literal["raw", "classification", "proportion"]


@dataclass(frozen=True)
class AnalysisAgentSlot:
    id: str
    kind: SlotKind
    title: str
    source_item_ids: tuple[str, ...] = ()
    narrative_instruction: str = ""
    table_id: str = ""
    template_id: str = ""
    static_body: str = ""
    table_kind: TableKind | None = None
    chart_when_table: bool = True
    mandatory_data: bool = False
    max_nl2sql_retries: int = 2
    max_synthesize_retries: int = 1
    allow_human_confirm: bool = False
    stream_live: bool = False
    # llm_section 蓝图（来自 configs/analysis_agent_reports/*.json）
    outline: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    allowed_outputs: tuple[str, ...] = ()
    field_hints: tuple[tuple[str, str], ...] = ()  # (label, hint)
    use_emit_tools: bool = False


@dataclass
class SlotOutput:
    slot_id: str
    kind: SlotKind
    title: str
    markdown: str
    table: dict[str, Any] | None = None
    chart: dict[str, Any] | None = None
    charts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
