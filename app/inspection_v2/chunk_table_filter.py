"""是否含表格：用于异步任务仅对含表分块调 LLM，与 docx_v2 / legacy 分块格式对齐。"""

from __future__ import annotations

import re

from app.inspection_v2.docx_v2_table_parse import CELL_RE, ROW_RE

_DOCX_V2_TABLE_MARK = "[DOCX_V2_TABLE"
_VMERGE_CONTINUE = "[vmerge续=与上格同列合并]"
_HEADER_COLS_RE = re.compile(r"cols=(\d+)")


def chunk_contains_table(chunk: str, *, parse_route: str) -> bool:
    pr = (parse_route or "text").strip().lower()
    if pr == "docx_v2":
        return _DOCX_V2_TABLE_MARK in (chunk or "")
    return _legacy_chunk_looks_like_table(chunk)


def _legacy_chunk_looks_like_table(chunk: str) -> bool:
    """legacy 分块：至少两行含 | 视为表格上下文。"""
    lines = [ln for ln in (chunk or "").splitlines() if "|" in ln]
    return len(lines) >= 2


def _collect_table_block_lines(lines: list[str], start: int) -> tuple[list[str], int]:
    """从 start（须为 DOCX_V2_TABLE 行）收集整块表行，返回 (block_lines, next_index)。"""
    tbl_lines = [lines[start].rstrip()]
    i = start + 1
    while i < len(lines):
        st = lines[i].strip()
        if st.startswith(_DOCX_V2_TABLE_MARK):
            break
        if ROW_RE.match(st):
            tbl_lines.append(lines[i].rstrip())
            i += 1
            continue
        if not st:
            i += 1
            continue
        break
    return tbl_lines, i


def _split_docx_v2_table_line_blocks(text: str) -> list[list[str]]:
    """将文本拆成多个表格块（每块首行为 [DOCX_V2_TABLE ...]）。"""
    lines = (text or "").splitlines()
    blocks: list[list[str]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped.startswith(_DOCX_V2_TABLE_MARK):
            i += 1
            continue
        tbl_lines, i = _collect_table_block_lines(lines, i)
        blocks.append(tbl_lines)
    return blocks


def extract_docx_v2_table_blocks_for_llm(chunk: str) -> str:
    """
    从 parse 分块中提取「heading/prelude + 表格」供 LLM user 消息使用。

    保留 [处理单元 heading_path=...] 与表前正文（受热面/规格等依赖 prelude）；
    丢弃表后无关正文。guard / 落盘仍应使用完整 chunk。
    """
    text = (chunk or "").strip()
    if not text or _DOCX_V2_TABLE_MARK not in text:
        return ""

    lines = text.splitlines()
    out_parts: list[str] = []
    preamble: list[str] = []
    i = 0
    while i < len(lines):
        st = lines[i].strip()
        if st.startswith(_DOCX_V2_TABLE_MARK):
            tbl_lines, i = _collect_table_block_lines(lines, i)
            block_lines = [ln for ln in preamble if ln.strip()] + tbl_lines
            out_parts.append("\n".join(block_lines).strip())
            preamble = []
            continue
        preamble.append(lines[i].rstrip())
        i += 1

    return "\n\n".join(p for p in out_parts if p).strip()


def _cell_part_nonempty(part: str) -> bool:
    cm = CELL_RE.search(part)
    if not cm:
        return False
    text = (cm.group(4) or "").strip()
    if text and text != _VMERGE_CONTINUE:
        return True
    if "[颜色标注:" in part or "[超标候选" in part:
        return True
    return False


def _table_column_nonempty_map(lines: list[str]) -> tuple[int, dict[int, bool]]:
    max_col = -1
    col_has: dict[int, bool] = {}

    header = lines[0].strip() if lines else ""
    hm = _HEADER_COLS_RE.search(header)
    if hm:
        max_col = max(max_col, int(hm.group(1)) - 1)

    for ln in lines:
        m = ROW_RE.match(ln.strip())
        if not m:
            continue
        for part in m.group(2).split("|"):
            part = part.strip()
            cm = CELL_RE.search(part)
            if not cm:
                continue
            c0 = int(cm.group(2))
            c1 = int(cm.group(3)) if cm.group(3) else c0
            max_col = max(max_col, c1)
            val = _cell_part_nonempty(part)
            for c in range(c0, c1 + 1):
                col_has[c] = col_has.get(c, False) or val

    return max_col, col_has


def _count_trailing_empty_columns(max_col: int, col_has: dict[int, bool]) -> int:
    if max_col < 0:
        return 0
    drop = 0
    for c in range(max_col, -1, -1):
        if col_has.get(c, False):
            break
        drop += 1
    return drop


def _rebuild_cell_part(part: str, c0: int, c1: int) -> str:
    cm = CELL_RE.search(part)
    if not cm:
        return part.strip()
    text = cm.group(4) or ""
    rest_match = re.search(r"='([^']*)'(.*)$", part.strip())
    suffix = rest_match.group(2) if rest_match else ""
    suffix = re.sub(r"\[hmerge×\d+\]", "", suffix)

    if c1 > c0:
        col_key = f"c{c0}-c{c1}"
        hmerge = f"[hmerge×{c1 - c0 + 1}]"
    else:
        col_key = f"c{c0}"
        hmerge = ""

    return f"{col_key}='{text}'{hmerge}{suffix}"


def _rewrite_row_body(body: str, drop_from: int) -> str:
    kept: list[str] = []
    for part in body.split("|"):
        part = part.strip()
        if not part:
            continue
        cm = CELL_RE.search(part)
        if not cm:
            continue
        c0 = int(cm.group(2))
        c1 = int(cm.group(3)) if cm.group(3) else c0
        if c0 >= drop_from:
            continue
        new_c1 = min(c1, drop_from - 1)
        kept.append(_rebuild_cell_part(part, c0, new_c1))
    return " | ".join(kept)


def _strip_one_table_block(lines: list[str]) -> list[str]:
    if not lines:
        return lines

    max_col, col_has = _table_column_nonempty_map(lines)
    trailing = _count_trailing_empty_columns(max_col, col_has)
    if trailing <= 0:
        return lines

    drop_from = max_col - trailing + 1
    new_cols = drop_from

    out: list[str] = []
    header = lines[0]
    if _HEADER_COLS_RE.search(header):
        out.append(_HEADER_COLS_RE.sub(f"cols={new_cols}", header, count=1))
    else:
        out.append(header)

    for ln in lines[1:]:
        m = ROW_RE.match(ln.strip())
        if not m:
            out.append(ln)
            continue
        new_body = _rewrite_row_body(m.group(2), drop_from)
        if new_body:
            out.append(f"r{m.group(1)}: {new_body}")
        else:
            out.append(f"r{m.group(1)}:")
    return out


def strip_trailing_empty_columns_for_llm(text: str) -> str:
    """
    裁掉表格块从右起连续全空列，并更新表头 cols=N（仅用于 LLM 输入）。
    保留 heading / prelude 等非表格行。
    """
    raw = (text or "").strip()
    if not raw or _DOCX_V2_TABLE_MARK not in raw:
        return raw

    lines = raw.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip().startswith(_DOCX_V2_TABLE_MARK):
            tbl_lines, i = _collect_table_block_lines(lines, i)
            out.extend(_strip_one_table_block(tbl_lines))
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip()


def resolve_llm_parse_chunk_body(
    chunk: str,
    *,
    table_only: bool,
    strip_trailing_empty_cols: bool = True,
) -> str:
    """
    LLM Parse user 消息正文：可选聚焦表格块（仍保留 heading/prelude），并裁 trailing 全空列。
    guard / 落盘仍应使用完整 chunk。
    """
    raw = chunk or ""
    if not table_only or _DOCX_V2_TABLE_MARK not in raw:
        return raw

    extracted = extract_docx_v2_table_blocks_for_llm(raw)
    body = extracted if extracted else raw
    if strip_trailing_empty_cols and _DOCX_V2_TABLE_MARK in body:
        body = strip_trailing_empty_columns_for_llm(body)
    return body


def filter_table_work_items(chunks: list[str], *, parse_route: str) -> list[tuple[int, str]]:
    """
    仅保留含表格的分块，按顺序编号 work_idx=1..N。
    返回 [(work_idx, chunk_text), ...]。
    """
    out: list[tuple[int, str]] = []
    for c in chunks:
        if chunk_contains_table(c, parse_route=parse_route):
            out.append((len(out) + 1, c))
    return out
