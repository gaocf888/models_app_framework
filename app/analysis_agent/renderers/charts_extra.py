"""从表格或声明式配置生成 bar / pie / line 图表 spec。"""

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


def _row_columns(rows: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for row in rows[:50]:
        if not isinstance(row, dict):
            continue
        for k in row.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                out.append(sk)
    return out


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


def chart_from_config(
    *,
    chart_id: str,
    chart_type: str,
    title: str,
    rows: list[dict[str, Any]],
    x_field: str = "",
    y_field: str = "",
    series_field: str = "",
    max_points: int = 60,
) -> dict[str, Any] | None:
    """声明式 bar / pie / line。字段空时自动挑标签列与数值列。"""
    if not rows:
        return None
    columns = _row_columns(rows)
    if not columns:
        return None
    y = y_field if y_field in columns else (_first_numeric_col(columns, rows) or "")
    x = x_field if x_field in columns else (_first_label_col(columns, y) or "")
    if not x or not y:
        return None
    series = series_field if series_field and series_field in columns else ""

    ctype = (chart_type or "bar").strip().lower()
    limit = max(1, int(max_points))

    if ctype == "pie":
        data: list[dict[str, Any]] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            try:
                val = float(row.get(y))
            except (TypeError, ValueError):
                continue
            data.append({"category": str(row.get(x, "")), "value": val})
        if not data:
            return None
        return {
            "id": chart_id,
            "chart_type": "pie",
            "title": title or chart_id,
            "spec": {
                "angleField": "value",
                "colorField": "category",
                "data": data,
            },
        }

    if ctype == "line":
        if series:
            series_map: dict[str, list[dict[str, Any]]] = {}
            for row in rows[: limit * 3]:
                if not isinstance(row, dict):
                    continue
                try:
                    val = float(row.get(y))
                except (TypeError, ValueError):
                    continue
                sname = str(row.get(series, "") or "series")
                series_map.setdefault(sname, []).append(
                    {"x": str(row.get(x, "")), "y": val}
                )
            if not series_map:
                return None
            # 截断各系列
            series_list = [
                {"name": name, "data": pts[:limit]}
                for name, pts in series_map.items()
                if pts
            ]
            return {
                "id": chart_id,
                "chart_type": "line",
                "title": title or chart_id,
                "spec": {
                    "xField": "x",
                    "yField": "y",
                    "seriesField": "name",
                    "series": series_list,
                },
            }
        data_line: list[dict[str, Any]] = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            try:
                val = float(row.get(y))
            except (TypeError, ValueError):
                continue
            data_line.append({"x": str(row.get(x, "")), "y": val})
        if not data_line:
            return None
        return {
            "id": chart_id,
            "chart_type": "line",
            "title": title or chart_id,
            "spec": {
                "xField": "x",
                "yField": "y",
                "data": data_line,
            },
        }

    # bar（默认）
    data_bar: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        try:
            val = float(row.get(y))
        except (TypeError, ValueError):
            continue
        data_bar.append({"zone": str(row.get(x, "")), "count": val})
    if not data_bar:
        return None
    return {
        "id": chart_id,
        "chart_type": "bar",
        "title": title or chart_id,
        "spec": {
            "xField": "zone",
            "yField": "count",
            "data": data_bar,
        },
    }
