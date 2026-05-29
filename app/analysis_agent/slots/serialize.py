from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.analysis_agent.slots.kinds import AnalysisAgentSlot


def slot_to_dict(slot: AnalysisAgentSlot) -> dict[str, Any]:
    return asdict(slot)


def slot_from_dict(data: dict[str, Any]) -> AnalysisAgentSlot:
    source = data.get("source_item_ids") or ()
    if isinstance(source, list):
        source = tuple(source)
    return AnalysisAgentSlot(
        id=str(data["id"]),
        kind=data["kind"],
        title=str(data.get("title") or ""),
        source_item_ids=source,
        narrative_instruction=str(data.get("narrative_instruction") or ""),
        table_id=str(data.get("table_id") or ""),
        template_id=str(data.get("template_id") or ""),
        static_body=str(data.get("static_body") or ""),
        table_kind=data.get("table_kind"),
        chart_when_table=bool(data.get("chart_when_table", True)),
        mandatory_data=bool(data.get("mandatory_data", False)),
        max_nl2sql_retries=int(data.get("max_nl2sql_retries", 2)),
        max_synthesize_retries=int(data.get("max_synthesize_retries", 1)),
        allow_human_confirm=bool(data.get("allow_human_confirm", False)),
        stream_live=bool(data.get("stream_live", False)),
    )
