"""
从 nl2sql_schema 等 RAG 片段中解析「表级中文说明 + 列级注释」。

支持摄入管线解析后的：
- `[DOCX_TABLE ...]` 表格行（`|` / 全角 `｜` 分列）
- `[XLSX_SHEET name=...]` 后的管道分隔行
- 标题行中的 `中文名（physical_table）` 形式
- 约定行 `[NL2SQL_TABLE_MAP]` / `[NL2SQL_TABLE_DEF]`（可选）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List


@dataclass
class TableRAGHints:
    """单张物理表在文档侧的语义补充（以库反射为准，此处仅注释）。"""

    zh_label: str | None = None
    category: str | None = None
    column_comments: dict[str, str] = field(default_factory=dict)


_TITLE_TABLE_RE = re.compile(
    r"[\u4e00-\u9fff\w\s\-]+[（(]\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[）)]",
)
_TABLE_MAP_TAG = re.compile(r"^\[NL2SQL_TABLE_MAP\]\s*(.*)$", re.IGNORECASE)
_TABLE_DEF_TAG = re.compile(r"^\[NL2SQL_TABLE_DEF\]\s*(.*)$", re.IGNORECASE)
_PHYSICAL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_DOCX_TABLE_HDR = re.compile(r"^\[DOCX_TABLE\b")
_XLSX_SHEET_HDR = re.compile(r"^\[XLSX_SHEET\b")


def _split_row_cells(line: str) -> list[str]:
    raw = line.replace("｜", "|")
    return [c.strip() for c in raw.split("|")]


def _norm_header_cell(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def _pick_col_index(headers: list[str], *keywords: str) -> int | None:
    for idx, h in enumerate(headers):
        hn = _norm_header_cell(h)
        for kw in keywords:
            if kw in hn:
                return idx
    return None


def _merge_hints(dst: dict[str, TableRAGHints], table: str, incoming: TableRAGHints) -> None:
    key = table.lower()
    cur = dst.get(key) or TableRAGHints()
    if incoming.zh_label and (not cur.zh_label or len(incoming.zh_label) > len(cur.zh_label)):
        cur.zh_label = incoming.zh_label
    if incoming.category and (not cur.category or len(incoming.category) > len(incoming.category)):
        cur.category = incoming.category
    for col, cmt in incoming.column_comments.items():
        cl = col.lower()
        if cl not in cur.column_comments or len(cmt) > len(cur.column_comments[cl]):
            cur.column_comments[cl] = cmt
    dst[key] = cur


def _parse_table_map_kv(rest: str) -> tuple[str, TableRAGHints] | None:
    """解析 `[NL2SQL_TABLE_MAP] k=v | k=v ...`。"""
    rest = rest.replace("｜", "|")
    parts = [p.strip() for p in rest.split("|") if p.strip()]
    data: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        data[k.strip().lower()] = v.strip()
    table = data.get("table")
    if not table or not _PHYSICAL_NAME.match(table):
        return None
    h = TableRAGHints(
        zh_label=data.get("zh") or data.get("zh_title"),
        category=data.get("category"),
    )
    return table.lower(), h


def _consume_structured_table_rows(
    rows: list[list[str]],
    current_table: str | None,
    out: dict[str, TableRAGHints],
) -> None:
    if not rows:
        return
    header = rows[0]
    hn = [_norm_header_cell(h) for h in header]

    # 总表：类别 | 中文描述 | 数据库表名 | 备注
    idx_tbl = _pick_col_index(header, "数据库表名", "物理表名")
    idx_zh = _pick_col_index(header, "中文描述", "描述", "表中文")
    idx_cat = _pick_col_index(header, "类别", "分类")
    if idx_tbl is not None and idx_zh is not None:
        for row in rows[1:]:
            if idx_tbl >= len(row):
                continue
            tname = row[idx_tbl].strip()
            if not _PHYSICAL_NAME.match(tname):
                continue
            zh = row[idx_zh].strip() if idx_zh < len(row) else ""
            cat = row[idx_cat].strip() if idx_cat is not None and idx_cat < len(row) else ""
            h = TableRAGHints(zh_label=zh or None, category=cat or None)
            _merge_hints(out, tname, h)
        return

    # 字段表：字段名 | 类型 | 注释
    idx_name = _pick_col_index(header, "字段名", "列名")
    idx_comment = _pick_col_index(header, "注释", "说明", "备注")
    if idx_name is not None and idx_comment is not None and current_table:
        for row in rows[1:]:
            if idx_name >= len(row):
                continue
            cname = row[idx_name].strip()
            if not _PHYSICAL_NAME.match(cname):
                continue
            cmt = row[idx_comment].strip() if idx_comment < len(row) else ""
            if not cmt:
                continue
            h = TableRAGHints(column_comments={cname.lower(): cmt})
            _merge_hints(out, current_table, h)
        return

    # 宽松：首列像字段名、末列像中文说明（无表头命中时）
    if current_table and len(header) >= 2:
        for row in rows[1:]:
            if not row:
                continue
            c0 = row[0].strip()
            if not _PHYSICAL_NAME.match(c0):
                continue
            tail = row[-1].strip()
            if tail and not _PHYSICAL_NAME.match(tail) and any("\u4e00" <= ch <= "\u9fff" for ch in tail):
                _merge_hints(
                    out,
                    current_table,
                    TableRAGHints(column_comments={c0.lower(): tail}),
                )


def parse_nl2sql_schema_snippets(snippets: Iterable[str]) -> dict[str, TableRAGHints]:
    """
    从多条 RAG 片段文本中合并解析出：物理表名 -> 文档侧语义提示。

    按文档顺序扫描：标题行 `中文（physical_table）` 会作用于紧随其后的字段表。
    """
    merged: dict[str, TableRAGHints] = {}
    for snippet in snippets:
        lines = (snippet or "").splitlines()
        current_table: str | None = None
        i = 0
        while i < len(lines):
            raw = lines[i].strip()
            if not raw:
                i += 1
                continue

            if _TABLE_MAP_TAG.match(raw):
                rest = _TABLE_MAP_TAG.match(raw).group(1).strip()  # type: ignore[union-attr]
                parsed = _parse_table_map_kv(rest)
                if parsed:
                    tkey, hint = parsed
                    _merge_hints(merged, tkey, hint)
                i += 1
                continue

            if _TABLE_DEF_TAG.match(raw):
                rest = _TABLE_DEF_TAG.match(raw).group(1).strip()  # type: ignore[union-attr]
                for part in rest.split("|"):
                    part = part.strip()
                    if part.lower().startswith("table="):
                        current_table = part.split("=", 1)[1].strip().lower()
                i += 1
                continue

            if _DOCX_TABLE_HDR.match(raw) or _XLSX_SHEET_HDR.match(raw):
                i += 1
                rows: list[list[str]] = []
                while i < len(lines):
                    s = lines[i].strip()
                    if not s or _DOCX_TABLE_HDR.match(s) or _XLSX_SHEET_HDR.match(s):
                        break
                    rows.append(_split_row_cells(lines[i]))
                    i += 1
                if rows:
                    _consume_structured_table_rows(rows, current_table, merged)
                continue

            m_title = _TITLE_TABLE_RE.search(raw)
            if m_title and not raw.startswith("["):
                current_table = m_title.group(1).lower()

            i += 1

    return merged


def format_enriched_catalog_line(
    table_name: str,
    db_columns: list[str],
    hints: TableRAGHints | None,
    *,
    max_cols: int,
) -> str:
    """生成单行 enriched 目录项（多列折叠为一条，便于占位符注入）。"""
    parts: list[str] = []
    zh = hints.zh_label if hints else None
    cat = hints.category if hints else None
    prefix = f"- {table_name}"
    if zh:
        prefix += f" — {zh}"
    if cat:
        prefix += f" [{cat}]"
    col_parts: list[str] = []
    cc = hints.column_comments if hints else {}
    for c in db_columns[:max_cols]:
        cl = c.lower()
        if cl in cc:
            col_parts.append(f"{c}({cc[cl]})")
        else:
            col_parts.append(c)
    if col_parts:
        prefix += ": " + ", ".join(col_parts)
    return prefix
