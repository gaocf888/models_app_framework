"""声明式 tables[] / charts[]：从 gathered_data 程序渲染 Markdown 表与 ECharts-like spec。"""

from __future__ import annotations

from typing import Any

from app.analysis_agent.renderers.charts_extra import chart_from_config, chart_from_table
from app.analysis_agent.renderers.table_generic import emit_table_from_rows


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
    rows = _collect_rows(gathered_data, source_ids)
    max_rows = max(1, int(spec.get("max_rows") or 80))
    columns = _resolve_columns(spec.get("columns"), rows)
    title = str(spec.get("title") or tid)
    table_kind = str(spec.get("table_kind") or "raw") or "raw"
    md, tbl = emit_table_from_rows(
        columns=columns,
        rows=rows[:max_rows],
        title=title,
        table_id=tid,
        source_item_ids=source_ids,
        table_kind=table_kind if table_kind in ("raw", "classification", "proportion") else "raw",
    )
    tbl["configured"] = True
    tbl["attach_to_chapter"] = str(spec.get("attach_to_chapter") or "")
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
    if chart_type not in ("bar", "pie", "line"):
        chart_type = "bar"
    source_ids = [str(x) for x in (spec.get("source_item_ids") or []) if str(x).strip()]
    rows = _collect_rows(gathered_data, source_ids)
    if not rows:
        return None
    title = str(spec.get("title") or cid)
    ch = chart_from_config(
        chart_id=cid,
        chart_type=chart_type,
        title=title,
        rows=rows,
        x_field=str(spec.get("x_field") or "").strip(),
        y_field=str(spec.get("y_field") or "").strip(),
        series_field=str(spec.get("series_field") or "").strip(),
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
    return ch


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
