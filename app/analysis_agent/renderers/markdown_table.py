from __future__ import annotations

import re
from typing import Any


def _pick_columns(rows: list[dict], max_cols: int = 12) -> list[str]:
    if not rows:
        return []
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                keys.append(str(k))
            if len(keys) >= max_cols:
                break
    return keys


def _escape_md_cell(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).replace("|", "\\|").replace("\n", " ")
    return s[:200] if len(s) > 200 else s


def _table_heading_markdown(title: str, *, subsection: bool = False) -> str:
    t = (title or "").strip()
    if not t:
        return ""
    prefix = "####" if subsection or re.match(r"^\d+\.\d+\s", t) else "###"
    return f"{prefix} {t}"


def render_markdown_table(
    rows: list[dict],
    *,
    max_rows: int,
    title: str,
    empty_message: str | None = None,
    subsection: bool = False,
) -> tuple[str, dict[str, Any]]:
    heading = _table_heading_markdown(title, subsection=subsection)
    if not rows:
        msg = empty_message or "（无数据）"
        body = f"{heading}\n\n{msg}\n" if heading else f"{msg}\n"
        return body, {
            "id": "",
            "title": title,
            "format": "markdown",
            "content": msg,
            "columns": [],
            "rows": [],
            "row_count": 0,
        }
    cols = _pick_columns(rows)
    if not cols:
        body = f"{heading}\n\n（无法解析列）\n" if heading else "（无法解析列）\n"
        return body, {"title": title, "format": "markdown", "content": body, "columns": [], "rows": [], "row_count": 0}
    trimmed = rows[:max_rows]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    lines = [heading, "", header, sep] if heading else [header, sep]
    for row in trimmed:
        if not isinstance(row, dict):
            continue
        lines.append("| " + " | ".join(_escape_md_cell(row.get(c)) for c in cols) + " |")
    if len(rows) > max_rows:
        lines.append("")
        lines.append(f"> 共 {len(rows)} 条记录，仅展示前 {max_rows} 条。")
    md = "\n".join(lines) + "\n\n"
    table_rows = [{c: row.get(c) for c in cols} for row in trimmed if isinstance(row, dict)]
    return md, {
        "id": re.sub(r"[^\w\-]", "_", title)[:64],
        "title": title,
        "format": "markdown",
        "content": md,
        "columns": cols,
        "rows": table_rows,
        "row_count": len(rows),
        "truncated": len(rows) > max_rows,
    }
