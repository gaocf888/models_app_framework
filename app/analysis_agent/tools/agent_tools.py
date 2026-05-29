from __future__ import annotations

import json
from typing import Any

from app.analysis_agent.renderers import section_data as core
from app.analysis_agent.renderers.charts_extra import chart_from_table
from app.analysis_agent.renderers.table_generic import emit_table_from_rows
from app.analysis_agent.tools.slot_context import get_slot_tool_context
from app.core.logging import get_logger

logger = get_logger(__name__)


def _empty_artifacts() -> dict[str, list[Any]]:
    return {"tables": [], "charts": [], "table_markdowns": []}


def _artifacts() -> dict[str, list[Any]]:
    ctx = get_slot_tool_context()
    art = ctx.get("section_artifacts")
    if not isinstance(art, dict):
        art = _empty_artifacts()
        ctx["section_artifacts"] = art
    for key in ("tables", "charts", "table_markdowns"):
        if key not in art or not isinstance(art[key], list):
            art[key] = []
    return art


def _tool_get_slot_data() -> str:
    """返回当前槽绑定的 q 切片 JSON 摘要（只读）。"""
    ctx = get_slot_tool_context()
    gathered = ctx.get("gathered_data") or {}
    item_ids = ctx.get("source_item_ids") or ()
    subset = core.resolve_data_subset(gathered, tuple(item_ids), strict=True)
    max_chars = int(ctx.get("gathered_json_max_chars") or 12000)
    raw = json.dumps(subset, ensure_ascii=False, default=str)
    if len(raw) > max_chars:
        raw = raw[: max_chars - 20] + "…(truncated)"
    return raw or "{}"


def _tool_rag_retrieve_snippets(query: str) -> str:
    """检索业务 RAG 片段（scene=analysis）。"""
    ctx = get_slot_tool_context()
    hybrid = ctx.get("hybrid_rag")
    analysis_type = str(ctx.get("analysis_type") or "overheat_guidance")
    top_k = int(ctx.get("rag_top_k") or 4)
    if hybrid is None:
        return "（RAG 不可用）"
    try:
        q = f"{analysis_type} {query}".strip()
        snippets = list(hybrid.retrieve(q, top_k=top_k) or [])[:top_k]
        if not snippets:
            return "（无 RAG 片段）"
        return "\n".join(f"- {s[:600]}" for s in snippets)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag_retrieve tool failed: %s", exc)
        return f"（RAG 检索失败：{exc}）"


def _parse_json_arg(payload: str) -> dict[str, Any]:
    text = (payload or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _tool_emit_markdown_table(payload: str) -> str:
    """
    登记本章表格（通用渲染）。入参 JSON：
    {"title":"...", "columns":["列1","列2"], "rows":[{...}], "table_kind":"classification|proportion|raw"}
    """
    data = _parse_json_arg(payload)
    columns = data.get("columns") or []
    rows = data.get("rows") or []
    if not isinstance(columns, list):
        columns = []
    if not isinstance(rows, list):
        rows = []
    title = str(data.get("title") or "")
    table_kind = str(data.get("table_kind") or "raw") or "raw"
    ctx = get_slot_tool_context()
    slot_id = str(ctx.get("slot_id") or "section")
    md, tbl = emit_table_from_rows(
        columns=[str(c) for c in columns],
        rows=rows,
        title=title,
        table_id=str(data.get("table_id") or f"{slot_id}_table"),
        source_item_ids=list(ctx.get("source_item_ids") or ()),
        table_kind=table_kind if table_kind in ("raw", "classification", "proportion") else "raw",
    )
    art = _artifacts()
    art["tables"].append(tbl)
    art["table_markdowns"].append(md)
    return f"已登记表格「{title or tbl.get('id')}」，共 {len(rows)} 行。"


def _tool_emit_chart(payload: str) -> str:
    """
    登记本章图表。入参 JSON：
    {"chart_type":"bar|pie", "title":"...", "columns":[...], "rows":[...]}
    或 {"chart_type":"bar", "table": {已有 emit_markdown_table 结构}}
    """
    data = _parse_json_arg(payload)
    chart_type = str(data.get("chart_type") or "bar").lower()
    title = str(data.get("title") or "")
    table = data.get("table")
    if not isinstance(table, dict):
        columns = data.get("columns") or []
        rows = data.get("rows") or []
        _, table = emit_table_from_rows(
            columns=[str(c) for c in columns] if isinstance(columns, list) else [],
            rows=rows if isinstance(rows, list) else [],
            title=title,
            table_id=str(data.get("table_id") or "chart_src"),
        )
    table_kind = "proportion" if chart_type == "pie" else "classification"
    if chart_type not in ("pie", "bar"):
        chart_type = "bar"
    ch = chart_from_table(
        table_id=str(table.get("id") or "chart"),
        table_kind=table_kind,
        table=table,
        title=title or str(table.get("title") or ""),
    )
    if ch is None:
        return "（无法生成图表：数据不足或列类型不匹配）"
    art = _artifacts()
    art["charts"].append(ch)
    return f"已登记{chart_type}图「{ch.get('title', title)}」。"


def build_narrative_tools(*, include_emit: bool = False) -> list[Any]:
    """构建 LangChain StructuredTool 列表；依赖缺失时返回空。"""
    try:
        from langchain_core.tools import StructuredTool  # type: ignore[import-not-found]
    except ImportError:
        try:
            from langchain.tools import StructuredTool  # type: ignore[import-not-found]
        except ImportError:
            return []

    tools = [
        StructuredTool.from_function(
            func=_tool_get_slot_data,
            name="get_slot_data",
            description="获取当前报告章节绑定的数据库查询结果 JSON，撰写正文时数值仅可来自此工具输出。",
        ),
        StructuredTool.from_function(
            func=_tool_rag_retrieve_snippets,
            name="rag_retrieve",
            description="按问题检索业务知识库片段，仅作方法参考，不得作为数值来源。",
        ),
    ]
    if include_emit:
        tools.extend(
            [
                StructuredTool.from_function(
                    func=_tool_emit_markdown_table,
                    name="emit_markdown_table",
                    description=(
                        "将表格数据登记为本章结构化表（前端可渲染）。"
                        "入参为 JSON 字符串：title, columns, rows, 可选 table_kind=classification|proportion|raw。"
                    ),
                ),
                StructuredTool.from_function(
                    func=_tool_emit_chart,
                    name="emit_chart",
                    description=(
                        "登记 bar 或 pie 图。入参 JSON：chart_type=bar|pie, title, columns, rows；"
                        "分布/分类用 bar，占比用 pie。"
                    ),
                ),
            ]
        )
    return tools
