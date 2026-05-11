"""
检修 docx V2 序列化文本 → Processing Unit 分块。

原则：
- 同一逻辑表格完整落在单个块中（不跨块拆表）。
- 含表的块以「表」为单元：紧邻该表上方的正文与该表同块。
- 不含表的纯文本按 max_chunk_chars 切分。
"""

from __future__ import annotations

import re
from typing import NamedTuple

from app.core.logging import get_logger

logger = get_logger(__name__)

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


def _split_oversized_text_piece(text: str, *, budget_chars: int) -> list[str]:
    """纯文本按固定字符宽切片（仅用于无表块）。"""
    if len(text) <= budget_chars:
        return [text]
    return [text[i : i + budget_chars] for i in range(0, len(text), budget_chars)]


def _pack_segments_to_chunks(
    heading_label: str,
    segments: list[_Segment],
    *,
    max_chunk_chars: int,
) -> list[str]:
    header = f"[处理单元 heading_path={heading_label}]\n"
    body_budget = max(64, max_chunk_chars - len(header))
    chunks: list[str] = []
    pending_text_parts: list[str] = []

    for seg in segments:
        if seg.kind == "text":
            t = "\n".join(seg.lines).strip()
            if t:
                pending_text_parts.append(t)
            continue

        tbl = seg.lines
        if not tbl:
            continue
        prelude = "\n\n".join(pending_text_parts).strip()
        pending_text_parts.clear()
        tbl_full = "\n".join(tbl).strip()
        body = f"{prelude}\n{tbl_full}".strip() if prelude else tbl_full
        full_chunk = (header + body).rstrip()
        if len(full_chunk) > max_chunk_chars:
            logger.warning(
                "docx_v2 table chunk exceeds max_chunk_chars (atomic table+prelude) len=%s max=%s",
                len(full_chunk),
                max_chunk_chars,
            )
        chunks.append(full_chunk)

    if pending_text_parts:
        prelude = "\n\n".join(pending_text_parts).strip()
        if prelude:
            for frag in _split_oversized_text_piece(prelude, budget_chars=body_budget):
                chunks.append((header + frag).rstrip())

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
