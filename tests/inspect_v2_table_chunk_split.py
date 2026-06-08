"""
DOCX V2 大表切块（探针/验证用，独立于现网 processing_units）。

策略（务实版）：
1. 沿用 processing unit：小节 heading + 表前正文 + 表格。
2. 单表超过字符预算时：识别表头区 + 按数据行窗口切分，每窗复制完整表头行。
3. 可选：同一表头行存在多个非「上/下」hmerge 段时，按列拆逻辑子表（强信号才启用）。

后续验证稳定后可迁入 app.inspection_v2.processing_units。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

from app.inspection_v2.docx_v2_table_parse import (
    ROW_RE,
    TABLE_MARK,
    _DOWN_LABELS,
    _IDX_HEADER_LABELS,
    _UP_LABELS,
    parse_table_rows,
)
from app.inspection_v2.processing_units import (
    _Segment,
    _is_section_heading,
    _segment_unit_lines,
    _split_oversized_text_piece,
    segment_docx_v2_by_headings,
)

_DOCX_V2_TABLE_PREFIX = "[DOCX_V2_TABLE"
_TABLE_HEAD_RE = re.compile(
    r"^\[DOCX_V2_TABLE\s+idx=(\d+)\s+rows=(\d+)\s+cols=(\d+)([^\]]*)\]\s*$"
)
_HMERGE_PART = re.compile(
    r"c(\d+)(?:-c(\d+))?='([^']*)'(?:\[hmerge×\d+\])"
)
_FLOAT_IN_CELL = re.compile(r"-?\d+(?:\.\d+)?")
_COMBO_IN_CELL = re.compile(r"^\d+\s*-\s*\d+$")


@dataclass(frozen=True)
class TableRowSlice:
    """表格内一行原文（保留原始行文本）。"""

    row_index: int
    line: str


@dataclass(frozen=True)
class TableSplitMeta:
    table_idx: int
    cols: int
    data_start: int
    header_rows: tuple[int, ...]
    data_rows: tuple[int, ...]


class ColumnSlice(NamedTuple):
    col_lo: int
    col_hi: int
    label: str


def _parse_table_mark(line: str) -> tuple[int, int, int, str] | None:
    m = _TABLE_HEAD_RE.match(line.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), (m.group(4) or "").strip()


def _row_lines(table_lines: list[str]) -> list[TableRowSlice]:
    out: list[TableRowSlice] = []
    for ln in table_lines:
        m = ROW_RE.match(ln.strip())
        if m:
            out.append(TableRowSlice(int(m.group(1)), ln.rstrip()))
    return out


def _row_has_index_thk_header(body: str) -> bool:
    for part in body.split(" | "):
        part = part.strip()
        cm = re.search(r"='([^']*)'", part)
        if cm and (cm.group(1) or "").strip() in _IDX_HEADER_LABELS:
            return True
    return False


def _row_looks_like_data_row(body: str) -> bool:
    """至少一组「编号样 + 测量值样」单元格。"""
    cells: list[str] = []
    for part in body.split(" | "):
        cm = re.search(r"='([^']*)'", part)
        if cm:
            cells.append((cm.group(1) or "").strip())
    if len(cells) < 2:
        return False
    has_thk = any(_FLOAT_IN_CELL.search(c) for c in cells)
    has_idx = any(_COMBO_IN_CELL.fullmatch(c) or c.isdigit() or re.fullmatch(r"-?\d+", c) for c in cells if c)
    return bool(has_thk and has_idx)


def detect_table_data_start(table_lines: list[str]) -> int:
    """
    表头区最后一行的下一行即为数据区起始行号。
    优先：含「编号|测量值」表头行；其次：首个像数据行的行。
    """
    rows = _row_lines(table_lines)
    if not rows:
        return 0
    cells = parse_table_rows([ln for ln in table_lines if ln.strip().startswith("r")])
    max_r = max((ri for ri, _ in cells), default=rows[-1].row_index)
    for sl in rows:
        m = ROW_RE.match(sl.line.strip())
        if not m:
            continue
        if _row_has_index_thk_header(m.group(2)):
            return sl.row_index + 1
    for sl in rows:
        m = ROW_RE.match(sl.line.strip())
        if not m:
            continue
        if _row_looks_like_data_row(m.group(2)):
            return sl.row_index
    return rows[0].row_index + 1 if len(rows) > 1 else rows[0].row_index


def _filter_row_to_column_slice(line: str, col_lo: int, col_hi: int) -> str | None:
    """保留行内落在 [col_lo, col_hi] 的单元格片段。"""
    m = ROW_RE.match(line.strip())
    if not m:
        return None
    ri = m.group(1)
    kept: list[str] = []
    for part in m.group(2).split(" | "):
        part = part.strip()
        cm = re.search(r"c(\d+)(?:-c(\d+))?", part)
        if not cm:
            continue
        c0 = int(cm.group(1))
        c1 = int(cm.group(2)) if cm.group(2) else c0
        if c1 < col_lo or c0 > col_hi:
            continue
        kept.append(part)
    if not kept:
        return None
    return f"r{ri}: " + " | ".join(kept)


def _hmerge_column_slices_on_row(body: str) -> list[ColumnSlice]:
    slices: list[ColumnSlice] = []
    for part in body.split(" | "):
        part = part.strip()
        if "[hmerge" not in part:
            continue
        hm = _HMERGE_PART.search(part)
        if not hm:
            continue
        c0 = int(hm.group(1))
        c1 = int(hm.group(2)) if hm.group(2) else c0
        label = (hm.group(3) or "").strip()
        if not label or label in _UP_LABELS or label in _DOWN_LABELS:
            continue
        if c1 <= c0:
            continue
        slices.append(ColumnSlice(c0, c1, label))
    return slices


def detect_strong_column_splits(table_lines: list[str], *, data_start: int) -> list[ColumnSlice]:
    """
    强信号：表头区内某行存在 ≥2 个非空、非上/下的 hmerge 列段。
    取 hmerge 段最多的一行作为列切分依据。
    """
    best: list[ColumnSlice] = []
    for ln in table_lines:
        m = ROW_RE.match(ln.strip())
        if not m:
            continue
        ri = int(m.group(1))
        if ri >= data_start:
            continue
        segs = _hmerge_column_slices_on_row(m.group(2))
        if len(segs) >= 2 and len(segs) > len(best):
            best = segs
    return best


def _build_table_mark(
    *,
    table_idx: int,
    n_rows: int,
    n_cols: int,
    sub: str = "",
    data_span: str = "",
    col_span: str = "",
) -> str:
    extra = ""
    if sub:
        extra += f" sub={sub}"
    if data_span:
        extra += f" data={data_span}"
    if col_span:
        extra += f" cols_span={col_span}"
    return f"[DOCX_V2_TABLE idx={table_idx} rows={n_rows} cols={n_cols}{extra}]"


def _char_len(lines: list[str]) -> int:
    return len("\n".join(lines))


def _window_table_char_len(
    *,
    table_idx: int,
    n_cols: int,
    header: list[TableRowSlice],
    data_window: list[TableRowSlice],
    col_lo: int,
    col_hi: int,
    col_label: str,
) -> int:
    return _char_len(
        _assemble_table_part(
            table_idx=table_idx,
            n_cols=n_cols,
            header=header,
            data_window=data_window,
            sub="1/1",
            data_span="",
            col_lo=col_lo,
            col_hi=col_hi,
            col_label=col_label,
            col_span="",
        )
    )


def _max_window_table_char_len(
    *,
    table_idx: int,
    n_cols: int,
    header: list[TableRowSlice],
    windows: list[list[TableRowSlice]],
    col_lo: int,
    col_hi: int,
    col_label: str,
) -> int:
    if not windows:
        return 0
    return max(
        _window_table_char_len(
            table_idx=table_idx,
            n_cols=n_cols,
            header=header,
            data_window=w,
            col_lo=col_lo,
            col_hi=col_hi,
            col_label=col_label,
        )
        for w in windows
    )


def split_table_lines_by_row_windows(
    table_lines: list[str],
    *,
    max_table_chars: int,
    data_rows_per_window: int,
    enable_column_split: bool = False,
) -> list[list[str]]:
    """
    将单个 [DOCX_V2_TABLE] 行列表切为多份；每份含完整表头 + 一段数据行。
    若整表未超预算则返回原表 alone。
    """
    if not table_lines or not table_lines[0].strip().startswith(_DOCX_V2_TABLE_PREFIX):
        return [table_lines]

    if _char_len(table_lines) <= max_table_chars:
        return [table_lines]

    parsed = _parse_table_mark(table_lines[0])
    if not parsed:
        return [table_lines]
    table_idx, _rows_decl, n_cols, _ = parsed

    row_slices = _row_lines(table_lines)
    if not row_slices:
        return [table_lines]

    data_start = detect_table_data_start(table_lines)
    header = [sl for sl in row_slices if sl.row_index < data_start]
    data = [sl for sl in row_slices if sl.row_index >= data_start]

    if not data:
        return [table_lines]

    column_slices: list[ColumnSlice | None] = [None]
    if enable_column_split:
        strong = detect_strong_column_splits(table_lines, data_start=data_start)
        if len(strong) >= 2:
            column_slices = list(strong)

    out_groups: list[list[str]] = []

    for col_spec in column_slices:
        col_lo = col_spec.col_lo if col_spec else 0
        col_hi = col_spec.col_hi if col_spec else max(n_cols - 1, 0)
        col_label = col_spec.label if col_spec else ""

        per_window = max(1, data_rows_per_window)
        windows: list[list[TableRowSlice]] = []
        for i in range(0, len(data), per_window):
            windows.append(data[i : i + per_window])

        # 仅当某一窗（表头+数据）仍超 table 字符预算时缩小行窗口；不因「多窗」本身而缩小
        while per_window > 1:
            if (
                _max_window_table_char_len(
                    table_idx=table_idx,
                    n_cols=n_cols,
                    header=header,
                    windows=windows,
                    col_lo=col_lo,
                    col_hi=col_hi,
                    col_label=col_label,
                )
                <= max_table_chars
            ):
                break
            per_window = max(1, per_window // 2)
            windows = [data[i : i + per_window] for i in range(0, len(data), per_window)]

        total_parts = len(windows) * len(column_slices)
        part_no = 0
        for wi, window in enumerate(windows, start=1):
            part_no += 1
            sub = f"{part_no}/{total_parts}" if total_parts > 1 else ""
            data_span = f"r{window[0].row_index}-r{window[-1].row_index}" if window else ""
            col_span = f"c{col_lo}-c{col_hi}" if col_spec else ""
            part = _assemble_table_part(
                table_idx=table_idx,
                n_cols=n_cols if not col_spec else (col_hi - col_lo + 1),
                header=header,
                data_window=window,
                sub=sub,
                data_span=data_span,
                col_lo=col_lo,
                col_hi=col_hi,
                col_label=col_label,
                col_span=col_span,
            )
            out_groups.append(part)

    return out_groups or [table_lines]


def _assemble_table_part(
    *,
    table_idx: int,
    n_cols: int,
    header: list[TableRowSlice],
    data_window: list[TableRowSlice],
    sub: str,
    data_span: str,
    col_lo: int,
    col_hi: int,
    col_label: str,
    col_span: str,
) -> list[str]:
    body_rows = header + data_window
    if col_lo > 0 or col_hi < 999:
        filtered: list[str] = []
        for sl in body_rows:
            fl = _filter_row_to_column_slice(sl.line, col_lo, col_hi)
            if fl:
                filtered.append(fl)
        row_lines = filtered
    else:
        row_lines = [sl.line for sl in body_rows]

    n_rows = len(row_lines)
    mark = _build_table_mark(
        table_idx=table_idx,
        n_rows=n_rows,
        n_cols=n_cols,
        sub=sub,
        data_span=data_span,
        col_span=col_span or (f"'{col_label}'" if col_label else ""),
    )
    return [mark, *row_lines]


def _pack_segments_with_row_windows(
    heading_label: str,
    segments: list[_Segment],
    *,
    max_chunk_chars: int,
    data_rows_per_window: int,
    enable_column_split: bool,
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

        # 表体预算：扣除 heading + prelude（仅首块带 prelude）
        table_budget = body_budget
        if prelude:
            table_budget = max(256, body_budget - len(prelude) - 2)

        table_parts = split_table_lines_by_row_windows(
            tbl,
            max_table_chars=table_budget,
            data_rows_per_window=data_rows_per_window,
            enable_column_split=enable_column_split,
        )

        for pi, part_lines in enumerate(table_parts):
            tbl_full = "\n".join(part_lines).strip()
            use_prelude = prelude if pi == 0 else ""
            body = f"{use_prelude}\n{tbl_full}".strip() if use_prelude else tbl_full
            full_chunk = (header + body).rstrip()
            chunks.append(full_chunk)

    if pending_text_parts:
        prelude = "\n\n".join(pending_text_parts).strip()
        if prelude:
            for frag in _split_oversized_text_piece(prelude, budget_chars=body_budget):
                chunks.append((header + frag).rstrip())

    return [c for c in chunks if c.strip()]


def split_docx_v2_with_row_windows(
    parsed_text: str,
    *,
    max_chunk_chars: int = 6000,
    data_rows_per_window: int = 20,
    enable_column_split: bool = False,
) -> list[str]:
    """
    与 split_docx_v2_by_processing_units 相同的 heading/表前正文策略；
    超大表按行窗口（+ 可选列切）再拆。
    """
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
        packed = _pack_segments_with_row_windows(
            label,
            segs,
            max_chunk_chars=max_chunk_chars,
            data_rows_per_window=data_rows_per_window,
            enable_column_split=enable_column_split,
        )
        chunks.extend(packed)
    return chunks or [text[:max_chunk_chars]]


def table_only_chunks(chunks: list[str]) -> list[str]:
    """与 chunk_table_filter 一致：仅含 [DOCX_V2_TABLE 的块。"""
    return [c for c in chunks if TABLE_MARK in c]
