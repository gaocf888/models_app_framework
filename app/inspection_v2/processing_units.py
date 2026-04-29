"""
检修 docx V2 序列化文本 → Processing Unit 分块。

按小节标题切分单元，单元内再按长度打包；超大单元按表格块/表行继续切分。
"""

from __future__ import annotations

import re
from typing import NamedTuple

_DOCX_V2_TABLE_PREFIX = "[DOCX_V2_TABLE"
_ROW_LINE = re.compile(r"^\s*r\d+\s*:")


class _Segment(NamedTuple):
    kind: str  # "text" | "table"
    lines: list[str]


def _is_section_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    patterns = (
        r"^（[一二三四五六七八九十百千万]+）\s*\S",
        r"^（[一二三四五六七八九十百千万]+）\s*$",
        r"^[一二三四五六七八九十]+[、.,．]\s*\S",
        r"^\d{1,3}[、.,．]\s*\S",
        r"^第[一二三四五六七八九十\d]+[章节条节部分]\s*\S",
        r"^[（(]\d{1,2}[)）]\s*\S",
        r"^[（(][一二三四五六七八九十]+[)）]\s*\S",
    )
    return any(re.match(p, s) for p in patterns)


def _segment_unit_lines(lines: list[str]) -> list[_Segment]:
    """将单个单元内的行拆成正文段与 DOCX_V2 表格块。"""
    out: list[_Segment] = []
    i = 0
    buf: list[str] = []
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if stripped.startswith(_DOCX_V2_TABLE_PREFIX):
            if buf:
                out.append(_Segment("text", buf))
                buf = []
            tbl: list[str] = [raw]
            i += 1
            while i < len(lines):
                ln = lines[i]
                st = ln.strip()
                if st.startswith(_DOCX_V2_TABLE_PREFIX):
                    break
                if _ROW_LINE.match(st):
                    tbl.append(ln)
                    i += 1
                    continue
                if not st:
                    i += 1
                    continue
                break
            out.append(_Segment("table", tbl))
            continue
        buf.append(raw)
        i += 1
    if buf:
        out.append(_Segment("text", buf))
    return out


def _split_table_by_rows(table_lines: list[str], *, max_rows_per_piece: int) -> list[list[str]]:
    if not table_lines:
        return []
    header = table_lines[0]
    row_lines = [ln for ln in table_lines[1:] if _ROW_LINE.match(ln.strip())]
    if not row_lines:
        return [table_lines]
    pieces: list[list[str]] = []
    for k in range(0, len(row_lines), max_rows_per_piece):
        chunk_rows = row_lines[k : k + max_rows_per_piece]
        pieces.append([header, *chunk_rows])
    return pieces


def _pack_segments_to_chunks(
    heading_label: str,
    segments: list[_Segment],
    *,
    max_chunk_chars: int,
) -> list[str]:
    header = f"[处理单元 heading_path={heading_label}]\n"
    pieces: list[str] = []
    max_rows_split = 40

    for seg in segments:
        if seg.kind == "text":
            t = "\n".join(seg.lines).strip()
            if t:
                pieces.append(t)
            continue
        tbl = seg.lines
        if not tbl:
            continue
        full_text = "\n".join(tbl).strip()
        if len(header) + len(full_text) + 1 <= max_chunk_chars:
            pieces.append(full_text)
            continue
        for g in _split_table_by_rows(tbl, max_rows_per_piece=max_rows_split):
            gtxt = "\n".join(g).strip()
            if len(header) + len(gtxt) + 1 <= max_chunk_chars:
                pieces.append(gtxt)
            else:
                for sub in _split_table_by_rows(g, max_rows_per_piece=max(8, max_rows_split // 4)):
                    pieces.append("\n".join(sub).strip())

    chunks: list[str] = []
    cur = header
    for p in pieces:
        sep = "" if cur == header else "\n"
        joined = cur + sep + p
        if len(joined) <= max_chunk_chars:
            cur = joined
            continue
        if cur != header:
            chunks.append(cur.rstrip())
        if len(header + p) <= max_chunk_chars:
            cur = header + p
            continue
        i = 0
        while i < len(p):
            room = max_chunk_chars - len(header)
            frag = p[i : i + room]
            chunks.append((header + frag).rstrip())
            i += len(frag)
        cur = header

    if cur != header:
        chunks.append(cur.rstrip())
    return [c for c in chunks if c.strip()]


def segment_docx_v2_by_headings(lines: list[str]) -> list[tuple[str, list[str]]]:
    """返回 (heading_path, body_lines)。正文不含标题行；前言无标题。"""
    units: list[tuple[str, list[str]]] = []
    current_heading = ""
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, current_heading
        if not buffer:
            return
        label = current_heading if current_heading else "前言"
        units.append((label, list(buffer)))
        buffer.clear()

    for line in lines:
        if _is_section_heading(line):
            flush()
            buffer = []
            current_heading = line.strip()
        else:
            buffer.append(line)
    flush()
    return units


def split_docx_v2_by_processing_units(parsed_text: str, *, max_chunk_chars: int) -> list[str]:
    text = (parsed_text or "").strip()
    if not text:
        return []
    lines = text.splitlines()
    units = segment_docx_v2_by_headings(lines)
    chunks: list[str] = []
    for label, body_lines in units:
        if not body_lines:
            continue
        segs = _segment_unit_lines(body_lines)
        packed = _pack_segments_to_chunks(label, segs, max_chunk_chars=max_chunk_chars)
        chunks.extend(packed)
    return chunks or [text[:max_chunk_chars]]
