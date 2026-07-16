"""
DOCX V2 大表按「表头 + 数据行窗口」切分（供 processing_units 调用）。

单表超过字符预算时：识别表头区，按 data_rows_per_window 切数据行，每窗复制完整表头。
可选强信号列切（横排多 hmerge 子表，默认关闭）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

from app.inspection_v2.docx_v2_table_parse import (
    ROW_RE,
    _DOWN_LABELS,
    _is_idx_header_text,
    _UP_LABELS,
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
    row_index: int
    line: str


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
        if cm and _is_idx_header_text((cm.group(1) or "").strip()):
            return True
    return False


def _row_looks_like_data_row(body: str) -> bool:
    cells: list[str] = []
    for part in body.split(" | "):
        cm = re.search(r"='([^']*)'", part)
        if cm:
            cells.append((cm.group(1) or "").strip())
    if len(cells) < 2:
        return False
    has_thk = any(_FLOAT_IN_CELL.search(c) for c in cells)
    has_idx = any(
        _COMBO_IN_CELL.fullmatch(c) or c.isdigit() or re.fullmatch(r"-?\d+", c)
        for c in cells
        if c
    )
    return bool(has_thk and has_idx)


def detect_table_data_start(table_lines: list[str]) -> int:
    """表头区最后一行的下一行即为数据区起始行号。"""
    rows = _row_lines(table_lines)
    if not rows:
        return 0
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

    mark = _build_table_mark(
        table_idx=table_idx,
        n_rows=len(row_lines),
        n_cols=n_cols,
        sub=sub,
        data_span=data_span,
        col_span=col_span or (f"'{col_label}'" if col_label else ""),
    )
    return [mark, *row_lines]


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
        windows = [data[i : i + per_window] for i in range(0, len(data), per_window)]

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
        for window in windows:
            part_no += 1
            sub = f"{part_no}/{total_parts}" if total_parts > 1 else ""
            data_span = f"r{window[0].row_index}-r{window[-1].row_index}" if window else ""
            col_span = f"c{col_lo}-c{col_hi}" if col_spec else ""
            out_groups.append(
                _assemble_table_part(
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
            )

    return out_groups or [table_lines]
