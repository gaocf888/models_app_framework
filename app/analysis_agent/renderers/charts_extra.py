"""按 table_kind 从表格数据生成 bar / pie 图表 spec。"""

from __future__ import annotations

from typing import Any


def _first_numeric_col(columns: list[str], rows: list[dict[str, Any]]) -> str | None:
    for col in columns:
        for row in rows[:20]:
            val = row.get(col)
            if val is None:
                continue
            try:
                float(val)
                return col
            except (TypeError, ValueError):
                continue
    return None


def _first_label_col(columns: list[str], numeric: str | None) -> str | None:
    for col in columns:
        if col == numeric:
            continue
        return col
    return columns[0] if columns else None


def chart_from_table(
    *,
    table_id: str,
    table_kind: str | None,
    table: dict[str, Any],
    title: str = "",
) -> dict[str, Any] | None:
    if not table_kind or table_kind == "raw":
        return None
    rows = table.get("rows") or []
    columns = table.get("columns") or []
    if not rows or not columns:
        return None
    numeric = _first_numeric_col(columns, rows)
    label = _first_label_col(columns, numeric)
    if not label or not numeric:
        return None
    data: list[dict[str, Any]] = []
    for row in rows[:30]:
        if not isinstance(row, dict):
            continue
        try:
            y = float(row.get(numeric))
        except (TypeError, ValueError):
            continue
        data.append({"category": str(row.get(label, "")), "value": y})
    if not data:
        return None
    chart_title = title or table.get("title") or table_id
    if table_kind == "proportion":
        return {
            "id": f"{table_id}_pie",
            "chart_type": "pie",
            "title": chart_title,
            "spec": {
                "angleField": "value",
                "colorField": "category",
                "data": data,
            },
        }
    if table_kind == "classification":
        bar_data = [{"zone": d["category"], "count": d["value"]} for d in data]
        return {
            "id": f"{table_id}_bar",
            "chart_type": "bar",
            "title": chart_title,
            "spec": {
                "xField": "zone",
                "yField": "count",
                "data": bar_data,
            },
        }
    return None
