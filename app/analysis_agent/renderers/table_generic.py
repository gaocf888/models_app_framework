from __future__ import annotations

from typing import Any

from app.analysis_agent.renderers import markdown_table as core

_TABLE_MAX_ROWS = 80


def emit_table_from_rows(
    *,
    columns: list[str],
    rows: list[dict[str, Any]],
    title: str = "",
    style: str = "full",
    table_id: str = "",
    source_item_ids: list[str] | None = None,
    table_kind: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """通用表格：返回 markdown 片段与 structured table payload。"""
    _ = style
    norm_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if columns:
            norm_rows.append({c: row.get(c) for c in columns})
        else:
            norm_rows.append(dict(row))
    md, tbl = core.render_markdown_table(
        norm_rows,
        max_rows=_TABLE_MAX_ROWS,
        title=title or table_id or "数据表",
        empty_message="（待补充）",
        subsection=True,
    )
    tbl["id"] = table_id or tbl.get("id") or "table"
    if source_item_ids:
        tbl["source_item_ids"] = list(source_item_ids)
    if table_kind in ("raw", "classification", "proportion"):
        tbl["table_kind"] = table_kind
    return md, tbl
