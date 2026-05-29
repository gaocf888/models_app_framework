from __future__ import annotations

from typing import Any

from app.analysis_agent.slots.kinds import AnalysisAgentSlot, SlotKind, TableKind


def _as_tuple_str(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(x) for x in value if str(x).strip())


def _field_hints_tuple(value: Any) -> tuple[tuple[str, str], ...]:
    if not value:
        return ()
    if isinstance(value, dict):
        return tuple((str(k), str(v)) for k, v in value.items())
    out: list[tuple[str, str]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("label"):
                out.append((str(item["label"]), str(item.get("hint") or "")))
    return tuple(out)


def slot_from_dict(raw: dict[str, Any]) -> AnalysisAgentSlot:
    kind = str(raw.get("kind") or "llm_narrative")
    if kind not in (
        "llm_narrative",
        "llm_section",
        "table_deterministic",
        "chart_structured",
        "static_markdown",
        "template_deterministic",
    ):
        kind = "llm_narrative"
    source = raw.get("source_item_ids") or ()
    if isinstance(source, list):
        source = tuple(str(x) for x in source)
    elif isinstance(source, str):
        source = (source,)
    else:
        source = tuple(source)

    tk = raw.get("table_kind")
    table_kind: TableKind | None = tk if tk in ("raw", "classification", "proportion") else None
    allowed = _as_tuple_str(raw.get("allowed_outputs"))
    use_emit = bool(raw.get("use_emit_tools", kind == "llm_section" or bool(allowed)))

    return AnalysisAgentSlot(
        id=str(raw["id"]),
        kind=kind,  # type: ignore[arg-type]
        title=str(raw.get("title") or ""),
        source_item_ids=source,
        narrative_instruction=str(raw.get("narrative_instruction") or ""),
        table_id=str(raw.get("table_id") or raw["id"]),
        template_id=str(raw.get("template_id") or ""),
        static_body=str(raw.get("static_body") or ""),
        table_kind=table_kind,
        chart_when_table=bool(raw.get("chart_when_table", table_kind is not None)),
        mandatory_data=bool(raw.get("mandatory_data", False)),
        max_nl2sql_retries=int(raw.get("max_nl2sql_retries", 2)),
        max_synthesize_retries=int(raw.get("max_synthesize_retries", 1)),
        allow_human_confirm=bool(raw.get("allow_human_confirm", False)),
        stream_live=bool(raw.get("stream_live", False)),
        outline=_as_tuple_str(raw.get("outline")),
        constraints=_as_tuple_str(raw.get("constraints")),
        allowed_outputs=allowed,
        field_hints=_field_hints_tuple(raw.get("field_hints")),
        use_emit_tools=use_emit,
    )


def slots_from_spec_dict(data: dict[str, Any]) -> list[AnalysisAgentSlot]:
    items = data.get("slots")
    if not isinstance(items, list):
        raise ValueError("invalid_slots_spec:missing_slots_array")
    out: list[AnalysisAgentSlot] = []
    for raw in items:
        if isinstance(raw, dict) and raw.get("id"):
            out.append(slot_from_dict(raw))
    if not out:
        raise ValueError("invalid_slots_spec:empty_slots")
    return out
