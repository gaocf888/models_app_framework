"""声明式 tables[] / charts[]：从 gathered_data 程序渲染 Markdown 表与 ECharts-like spec。"""

from __future__ import annotations

from typing import Any

from app.analysis_agent.renderers.charts_extra import chart_from_config, chart_from_table
from app.analysis_agent.renderers.table_generic import emit_table_from_rows


def _round_numeric_rows(rows: list[dict[str, Any]], decimals: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        mapped = dict(row)
        for key, val in list(mapped.items()):
            if val is None or isinstance(val, bool) or str(val).strip() == "":
                continue
            try:
                mapped[key] = round(float(val), decimals)
            except (TypeError, ValueError):
                continue
        out.append(mapped)
    return out


def _insert_group_headers(
    rows: list[dict[str, Any]],
    *,
    group_field: str,
    title_key: str,
) -> list[dict[str, Any]]:
    if not group_field or not rows:
        return rows
    ordered = sorted(rows, key=lambda r: str(r.get(group_field) or ""))
    out: list[dict[str, Any]] = []
    current = object()
    for row in ordered:
        group = str(row.get(group_field) or "").strip()
        if group != current:
            current = group
            header = {k: "" for k in row.keys()}
            header[group_field] = group
            header[title_key] = group
            out.append(header)
        out.append(row)
    return out


def _collect_rows(
    gathered_data: dict[str, list[dict[str, Any]]],
    source_item_ids: list[str] | tuple[str, ...],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for iid in source_item_ids:
        for row in gathered_data.get(str(iid)) or []:
            if isinstance(row, dict):
                rows.append(dict(row))
    return rows


def _resolve_columns(
    columns: list[str] | None,
    rows: list[dict[str, Any]],
) -> list[str]:
    cols = [str(c) for c in (columns or []) if str(c).strip()]
    if cols:
        return cols
    if not rows:
        return []
    # 保序：首行键 + 后续新键
    seen: set[str] = set()
    out: list[str] = []
    for row in rows[:50]:
        for k in row.keys():
            sk = str(k)
            if sk not in seen:
                seen.add(sk)
                out.append(sk)
    return out


def _map_rows_by_column_keys(
    rows: list[dict[str, Any]],
    *,
    display_columns: list[str],
    column_keys: list[str] | None,
) -> list[dict[str, Any]]:
    """
    将 SQL 英文字段映射为展示列名。

    ``columns`` = 表头中文；``column_keys`` = 与 columns 等长的行字典键。
    未配置 column_keys 时原样返回（兼容旧板块）。
    """
    keys = [str(k).strip() for k in (column_keys or []) if str(k).strip()]
    if not keys or not display_columns or len(keys) != len(display_columns):
        return rows
    mapped: list[dict[str, Any]] = []
    for row in rows:
        mapped.append({display_columns[i]: row.get(keys[i]) for i in range(len(display_columns))})
    return mapped


def _chart_xy_fields(spec: dict[str, Any]) -> tuple[str, str, str]:
    """兼容 x_field/y_field 与历史误写 x_key/y_key。"""
    x = str(spec.get("x_field") or spec.get("x_key") or "").strip()
    y = str(spec.get("y_field") or spec.get("y_key") or "").strip()
    series = str(spec.get("series_field") or spec.get("series_key") or "").strip()
    return x, y, series


def _apply_row_filter(rows: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """循环章 row_filter: {field, equals}；字符串/数字宽松相等。"""
    filt = spec.get("row_filter")
    if not isinstance(filt, dict):
        return rows
    field = str(filt.get("field") or "").strip()
    if not field:
        return rows
    equals = filt.get("equals")
    out: list[dict[str, Any]] = []
    for row in rows:
        val = row.get(field)
        if val == equals or str(val) == str(equals):
            out.append(row)
    return out


def render_configured_table(
    spec: dict[str, Any],
    *,
    gathered_data: dict[str, list[dict[str, Any]]],
) -> tuple[str, dict[str, Any]] | None:
    """返回 (markdown, table_payload)；无数据时仍返回空表 payload（待补充）。"""
    tid = str(spec.get("id") or "").strip()
    if not tid:
        return None
    source_ids = [str(x) for x in (spec.get("source_item_ids") or []) if str(x).strip()]
    rows = _apply_row_filter(_collect_rows(gathered_data, source_ids), spec)
    max_rows = max(1, int(spec.get("max_rows") or 80))
    columns = _resolve_columns(spec.get("columns"), rows)
    col_keys = spec.get("column_keys") if isinstance(spec.get("column_keys"), list) else None
    group_field = str(spec.get("group_field") or "").strip()
    if group_field:
        title_key = str(spec.get("group_title_key") or ((col_keys[0] if col_keys else "") or (columns[0] if columns else ""))).strip()
        if title_key:
            rows = _insert_group_headers(rows, group_field=group_field, title_key=title_key)
    rows = _map_rows_by_column_keys(rows, display_columns=columns, column_keys=col_keys)
    if spec.get("round_decimals") is not None:
        rows = _round_numeric_rows(rows, int(spec.get("round_decimals") or 2))
    title = str(spec.get("title") or tid)
    table_kind = str(spec.get("table_kind") or "raw") or "raw"
    md, tbl = emit_table_from_rows(
        columns=columns,
        rows=rows,
        title=title,
        table_id=tid,
        source_item_ids=source_ids,
        table_kind=table_kind if table_kind in ("raw", "classification", "proportion") else "raw",
        max_rows=max_rows,
    )
    tbl["configured"] = True
    tbl["attach_to_chapter"] = str(spec.get("attach_to_chapter") or "")
    if spec.get("placeholder"):
        tbl["placeholder"] = True
    unit_line = str(spec.get("unit_line") or "").strip()
    if unit_line:
        md = f"{unit_line}\n\n{md}"
        tbl["unit_line"] = unit_line
    return md, tbl


def render_configured_chart(
    spec: dict[str, Any],
    *,
    gathered_data: dict[str, list[dict[str, Any]]],
    chart_mode: str = "auto",
) -> dict[str, Any] | None:
    if chart_mode == "off":
        return None
    cid = str(spec.get("id") or "").strip()
    if not cid:
        return None
    chart_type = str(spec.get("chart_type") or "bar").strip().lower() or "bar"
    source_ids = [str(x) for x in (spec.get("source_item_ids") or []) if str(x).strip()]
    rows = _apply_row_filter(_collect_rows(gathered_data, source_ids), spec)
    title = str(spec.get("title") or cid)
    x_field, y_field, series_field = _chart_xy_fields(spec)
    raw_y_fields = spec.get("y_fields") if isinstance(spec.get("y_fields"), list) else None
    if chart_type in ("dual_axis", "map_placeholder", "map_image"):
        ch = chart_from_config(
            chart_id=cid,
            chart_type=chart_type,
            title=title,
            rows=rows,
            x_field=x_field,
            y_field=y_field,
            series_field=series_field,
            y_fields=raw_y_fields,
            max_points=max(1, int(spec.get("max_points") or 60)),
            y_left=spec.get("y_left") if isinstance(spec.get("y_left"), dict) else None,
            y_right=spec.get("y_right") if isinstance(spec.get("y_right"), dict) else None,
            placeholder=bool(spec.get("placeholder")),
        )
        if ch:
            ch["configured"] = True
            ch["attach_to_chapter"] = str(spec.get("attach_to_chapter") or "")
            ch["source_item_ids"] = source_ids
            caption = str(spec.get("caption") or "").strip()
            if caption:
                ch["caption"] = caption
        return ch
    if chart_type not in ("bar", "pie", "line"):
        chart_type = "bar"
    if not rows:
        if spec.get("placeholder"):
            return {
                "id": cid,
                "chart_type": chart_type,
                "title": title,
                "placeholder": True,
                "configured": True,
                "attach_to_chapter": str(spec.get("attach_to_chapter") or ""),
                "source_item_ids": source_ids,
                "spec": {"data": []},
            }
        return None
    ch = chart_from_config(
        chart_id=cid,
        chart_type=chart_type,
        title=title,
        rows=rows,
        x_field=x_field,
        y_field=y_field,
        series_field=series_field,
        y_fields=raw_y_fields,
        max_points=max(1, int(spec.get("max_points") or 60)),
    )
    if ch is None:
        # 回退：按 table_kind 启发式
        tk = "proportion" if chart_type == "pie" else "classification"
        if chart_type == "line":
            return None
        cols = _resolve_columns(None, rows)
        _, tbl = emit_table_from_rows(
            columns=cols,
            rows=rows[:60],
            title=title,
            table_id=cid,
            source_item_ids=source_ids,
            table_kind=tk,
        )
        ch = chart_from_table(table_id=cid, table_kind=tk, table=tbl, title=title)
    if ch:
        ch["configured"] = True
        ch["attach_to_chapter"] = str(spec.get("attach_to_chapter") or "")
        ch["source_item_ids"] = source_ids
        caption = str(spec.get("caption") or "").strip()
        if caption:
            ch["caption"] = caption
        if spec.get("round_decimals") is not None:
            _round_chart_values(ch, int(spec.get("round_decimals") or 2))
    return ch


def _round_chart_values(chart: dict[str, Any], decimals: int) -> None:
    spec = chart.get("spec")
    if not isinstance(spec, dict):
        return
    data = spec.get("data")
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ("count", "value", "y"):
                if key in item:
                    try:
                        item[key] = round(float(item[key]), decimals)
                    except (TypeError, ValueError):
                        continue
    series = spec.get("series")
    if isinstance(series, list):
        for s in series:
            if not isinstance(s, dict):
                continue
            for key in ("data", "values"):
                vals = s.get(key)
                if not isinstance(vals, list):
                    continue
                rounded: list[Any] = []
                for val in vals:
                    try:
                        rounded.append(round(float(val), decimals))
                    except (TypeError, ValueError):
                        rounded.append(val)
                s[key] = rounded


def prepare_chapter_viz(
    *,
    chapter_id: str,
    report_tables: list[dict[str, Any]] | None,
    report_charts: list[dict[str, Any]] | None,
    gathered_data: dict[str, list[dict[str, Any]]],
    chart_mode: str = "auto",
) -> dict[str, Any]:
    """
    按 attach_to_chapter 过滤并渲染本章声明式表/图。

    返回：
    - tables / charts：结构化 payload
    - table_markdowns：Markdown 片段列表
    - note：注入 LLM 的简短说明
    """
    cid = (chapter_id or "").strip()
    tables_out: list[dict[str, Any]] = []
    charts_out: list[dict[str, Any]] = []
    mds: list[str] = []

    for spec in report_tables or []:
        if not isinstance(spec, dict):
            continue
        if str(spec.get("attach_to_chapter") or "").strip() != cid:
            continue
        rendered = render_configured_table(spec, gathered_data=gathered_data)
        if not rendered:
            continue
        md, tbl = rendered
        mds.append(md)
        tables_out.append(tbl)

    for spec in report_charts or []:
        if not isinstance(spec, dict):
            continue
        if str(spec.get("attach_to_chapter") or "").strip() != cid:
            continue
        ch = render_configured_chart(
            spec, gathered_data=gathered_data, chart_mode=chart_mode
        )
        if ch:
            charts_out.append(ch)

    note_lines: list[str] = []
    for t in tables_out:
        note_lines.append(
            f"- 表 `{t.get('id')}`：{t.get('title') or t.get('id')}（{len(t.get('rows') or [])} 行，已 SSE 推送）"
        )
    for c in charts_out:
        note_lines.append(
            f"- 图 `{c.get('id')}`：{c.get('title') or c.get('id')}（{c.get('chart_type')}，已 SSE 推送）"
        )
    note = ""
    if note_lines:
        note = "本章已由程序按报告配置渲染以下表/图，请勿再用工具重复 emit：\n" + "\n".join(
            note_lines
        )

    return {
        "tables": tables_out,
        "charts": charts_out,
        "table_markdowns": mds,
        "note": note,
    }
