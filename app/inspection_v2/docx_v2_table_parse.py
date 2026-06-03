"""
DOCX V2 分块内 [DOCX_V2_TABLE] 行文本解析（供 color_guard / tube_thickness_bind_guard 共用）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

TABLE_MARK = "[DOCX_V2_TABLE"
ROW_RE = re.compile(r"^r(\d+):\s*(.+)$")
COL_GROUP_RE = re.compile(
    r"(c(\d+))(?:-c(\d+))?='([^']*)'(?:\[hmerge×\d+\])?",
)
CELL_RE = re.compile(
    r"(c(\d+))(?:-c(\d+))?='([^']*)'"
    r"(?:\[hmerge×\d+\])?"
    r"(?:\[颜色标注:([^\]]+)\])?"
    r"(?:\[超标候选[^]]*\])?",
)

_UP_LABELS = frozenset({"上", "向上", "上数", "上部"})
_DOWN_LABELS = frozenset({"下", "向下", "下数", "下部"})
_IDX_HEADER_LABELS = frozenset({"编号", "根数", "序号"})
_THK_HEADER_LABELS = frozenset({"测量值", "测厚", "厚度", "壁厚"})
_THK_TOLERANCE = 0.03
_COMBO_INDEX_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")
_WIDE_DIRECTION_SPAN = 4  # hmerge 跨度 ≥4 列时，在 span 内按「编号|测量值」拆列组


def thk_close(a: float, b: float, tol: float = _THK_TOLERANCE) -> bool:
    return abs(a - b) <= tol


def parse_float_cell(text: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", (text or "").strip())
    if not m:
        return None
    return float(m.group(0))


def parse_int_cell(text: str) -> int | None:
    t = (text or "").strip()
    if not re.fullmatch(r"-?\d+", t):
        return None
    return int(t)


def parse_combo_cell(text: str) -> tuple[str, str] | None:
    """组合编号单元格（如 2-1）→ (行号段, 管号段)。"""
    m = _COMBO_INDEX_RE.fullmatch((text or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def cell_defect_by_markup(color_note: str | None, part: str) -> bool:
    if color_note and "高亮" in color_note:
        return True
    if "[超标候选" in part:
        return True
    if color_note and "底纹=" in color_note:
        return True
    return False


@dataclass
class DirectionSpan:
    direction: str
    col_lo: int
    col_hi: int
    header_row: int


@dataclass
class DirectionGroup:
    direction: str  # 上 | 下
    idx_col: int
    thk_col: int
    header_row: int


@dataclass
class ScopeSegment:
    header_row: int
    col_lo: int
    col_hi: int
    label: str


@dataclass
class IndexPair:
    """表格中一组「编号列+壁厚列」事实。"""

    scope_label: str
    direction: str
    row_ri: int
    idx_col: int
    thk_col: int
    index_val: int
    thickness: float
    defect_by_color: bool = False


@dataclass
class ComboIndexCell:
    """编号列为组合编号（如 2-1）的单元格事实。"""

    scope_label: str
    direction: str
    row_ri: int
    idx_col: int
    thk_col: int
    raw: str
    row_part: str
    tube_part: str
    thickness: float | None = None


@dataclass
class ParsedTable:
    lines: list[str]
    cells: dict[tuple[int, int], dict[str, Any]] = field(default_factory=dict)
    pairs: list[IndexPair] = field(default_factory=list)
    combo_cells: list[ComboIndexCell] = field(default_factory=list)
    groups: list[DirectionGroup] = field(default_factory=list)


def _parse_direction_spans_from_row(body: str, header_row: int) -> list[DirectionSpan]:
    spans: list[DirectionSpan] = []
    for gm in COL_GROUP_RE.finditer(body):
        c0 = int(gm.group(2))
        c1 = int(gm.group(3)) if gm.group(3) else c0
        label = (gm.group(4) or "").strip()
        if label in _UP_LABELS:
            direction = "上"
        elif label in _DOWN_LABELS:
            direction = "下"
        else:
            continue
        if c1 - c0 >= 1:
            spans.append(DirectionSpan(direction=direction, col_lo=c0, col_hi=c1, header_row=header_row))
    return spans


def _parse_column_groups_from_row(body: str, header_row: int) -> list[DirectionGroup]:
    """窄跨度 上/下（hmerge×2）：直接 (c0,c1) 列组。宽跨度由 _build_direction_groups_for_band 处理。"""
    groups: list[DirectionGroup] = []
    for span in _parse_direction_spans_from_row(body, header_row):
        width = span.col_hi - span.col_lo + 1
        if width == 2:
            groups.append(
                DirectionGroup(
                    direction=span.direction,
                    idx_col=span.col_lo,
                    thk_col=span.col_lo + 1,
                    header_row=header_row,
                )
            )
        elif width < _WIDE_DIRECTION_SPAN:
            groups.append(
                DirectionGroup(
                    direction=span.direction,
                    idx_col=span.col_lo,
                    thk_col=span.col_hi,
                    header_row=header_row,
                )
            )
    return groups


def _find_number_header_row(
    cells: dict[tuple[int, int], dict[str, Any]],
    band_start: int,
    band_end: int,
    *,
    after_row: int | None,
) -> int | None:
    start = (after_row + 1) if after_row is not None else band_start + 1
    for ri in range(start, min(band_end, start + 6) + 1):
        for ci in range(0, 32):
            text = (cells.get((ri, ci)) or {}).get("text", "").strip()
            if text in _IDX_HEADER_LABELS:
                return ri
    return None


def _idx_thk_pairs_in_span(
    cells: dict[tuple[int, int], dict[str, Any]],
    header_ri: int,
    col_lo: int,
    col_hi: int,
) -> list[tuple[int, int]]:
    """在列范围内识别「编号|测量值」相邻列对。"""
    pairs: list[tuple[int, int]] = []
    ci = col_lo
    while ci + 1 <= col_hi:
        t0 = (cells.get((header_ri, ci)) or {}).get("text", "").strip()
        t1 = (cells.get((header_ri, ci + 1)) or {}).get("text", "").strip()
        if t0 in _IDX_HEADER_LABELS and (t1 in _THK_HEADER_LABELS or ci + 1 <= col_hi):
            pairs.append((ci, ci + 1))
            ci += 2
        else:
            ci += 1
    return pairs


def _groups_for_direction_span(
    span: DirectionSpan,
    *,
    cells: dict[tuple[int, int], dict[str, Any]],
    header_row: int | None,
) -> list[DirectionGroup]:
    width = span.col_hi - span.col_lo + 1
    if width == 2:
        return [
            DirectionGroup(
                direction=span.direction,
                idx_col=span.col_lo,
                thk_col=span.col_lo + 1,
                header_row=span.header_row,
            )
        ]
    if width >= _WIDE_DIRECTION_SPAN and header_row is not None:
        pairs = _idx_thk_pairs_in_span(cells, header_row, span.col_lo, span.col_hi)
        if pairs:
            return [
                DirectionGroup(
                    direction=span.direction,
                    idx_col=idx_col,
                    thk_col=thk_col,
                    header_row=span.header_row,
                )
                for idx_col, thk_col in pairs
            ]
    if width >= _WIDE_DIRECTION_SPAN:
        out: list[DirectionGroup] = []
        c = span.col_lo
        while c + 1 <= span.col_hi:
            out.append(
                DirectionGroup(
                    direction=span.direction,
                    idx_col=c,
                    thk_col=c + 1,
                    header_row=span.header_row,
                )
            )
            c += 2
        return out
    return [
        DirectionGroup(
            direction=span.direction,
            idx_col=span.col_lo,
            thk_col=span.col_hi,
            header_row=span.header_row,
        )
    ]


def _max_col_in_band(cells: dict[tuple[int, int], dict[str, Any]], band_start: int, band_end: int) -> int:
    mx = 0
    for ri, ci in cells:
        if band_start <= ri <= band_end:
            mx = max(mx, ci)
    return mx


def _build_direction_groups_for_band(
    lines: list[str],
    cells: dict[tuple[int, int], dict[str, Any]],
    band_start: int,
    band_end: int,
) -> tuple[list[DirectionGroup], int]:
    """
    按 band 解析 DirectionGroup 列表与数据区起始行。

    优先级：窄 上/下(hmerge×2) → 宽 上/下+编号|测量值表头 → 无方向仅编号表头 → 默认 (0,1)(2,3)。
    """
    dir_row: int | None = None
    direction_spans: list[DirectionSpan] = []
    for ri in range(band_start + 1, band_end + 1):
        line = next((ln for ln in lines if ln.startswith(f"r{ri}:")), "")
        if not line:
            continue
        spans = _parse_direction_spans_from_row(line.split(":", 1)[1], ri)
        if spans:
            dir_row = ri
            direction_spans = spans
            break

    if direction_spans:
        header_row = _find_number_header_row(
            cells, band_start, band_end, after_row=dir_row
        )
        groups: list[DirectionGroup] = []
        for span in direction_spans:
            groups.extend(
                _groups_for_direction_span(span, cells=cells, header_row=header_row)
            )
        if groups:
            data_start = (header_row + 1) if header_row is not None else (dir_row + 1)
            return groups, data_start

    header_row = _find_number_header_row(
        cells, band_start, band_end, after_row=band_start
    )
    if header_row is not None:
        col_hi = _max_col_in_band(cells, band_start, band_end)
        pairs = _idx_thk_pairs_in_span(cells, header_row, 0, col_hi)
        if pairs:
            groups = [
                DirectionGroup("下", idx_col, thk_col, header_row)
                for idx_col, thk_col in pairs
            ]
            return groups, header_row + 1

    return [
        DirectionGroup("上", 0, 1, band_start),
        DirectionGroup("下", 2, 3, band_start),
    ], band_start + 1


def parse_column_groups(lines: list[str]) -> list[DirectionGroup]:
    """全表按 band 解析列组（供 color_guard 等使用）。"""
    cells = parse_table_rows(lines)
    bands = _row_bands(lines)
    out: list[DirectionGroup] = []
    for band_start, band_end in bands:
        groups, _ = _build_direction_groups_for_band(lines, cells, band_start, band_end)
        out.extend(groups)
    if not out:
        out = [
            DirectionGroup("上", 0, 1, -1),
            DirectionGroup("下", 2, 3, -1),
        ]
    return out


def parse_table_rows(lines: list[str]) -> dict[tuple[int, int], dict[str, Any]]:
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for line in lines:
        m = ROW_RE.match(line)
        if not m:
            continue
        ri = int(m.group(1))
        body = m.group(2)
        for part in body.split(" | "):
            part = part.strip()
            cm = CELL_RE.search(part)
            if not cm:
                continue
            c0 = int(cm.group(2))
            c1 = int(cm.group(3)) if cm.group(3) else c0
            text = (cm.group(4) or "").strip()
            color_note = cm.group(5)
            defect = cell_defect_by_markup(color_note, part)
            for ci in range(c0, c1 + 1):
                cells[(ri, ci)] = {"text": text, "defect_by_color": defect}
    return cells


def _parse_hmerge_segments_from_line(line: str) -> list[ScopeSegment]:
    m = ROW_RE.match(line)
    if not m:
        return []
    ri = int(m.group(1))
    body = m.group(2)
    segs: list[ScopeSegment] = []
    for part in body.split(" | "):
        part = part.strip()
        if "[hmerge" not in part:
            continue
        cm = CELL_RE.search(part)
        if not cm:
            continue
        c0 = int(cm.group(2))
        c1 = int(cm.group(3)) if cm.group(3) else c0
        label = (cm.group(4) or "").strip()
        if label in _UP_LABELS or label in _DOWN_LABELS:
            continue
        if not label:
            continue
        segs.append(ScopeSegment(ri, c0, c1, label))
    return segs


def _max_row_index(lines: list[str]) -> int:
    mx = 0
    for line in lines:
        m = ROW_RE.match(line)
        if m:
            mx = max(mx, int(m.group(1)))
    return mx


def _scope_label_for_col(
    col: int,
    major: list[ScopeSegment],
    minor: list[ScopeSegment],
) -> str:
    parts: list[str] = []
    for seg in major:
        if seg.col_lo <= col <= seg.col_hi:
            parts.append(seg.label)
    for seg in minor:
        if seg.col_lo <= col <= seg.col_hi:
            parts.append(seg.label)
    return "".join(parts) if parts else ""


def _is_major_title(seg: ScopeSegment) -> bool:
    if len(seg.label) >= 8:
        return True
    return any(k in seg.label for k in ("水冷壁", "包墙", "过热器", "再热器", "省煤器", "吹灰器", "风孔"))


def _row_bands(lines: list[str]) -> list[tuple[int, int]]:
    title_rows: list[int] = []
    for line in lines:
        for seg in _parse_hmerge_segments_from_line(line):
            if _is_major_title(seg):
                title_rows.append(seg.header_row)
                break
    title_rows = sorted(set(title_rows))
    if not title_rows:
        return [(0, _max_row_index(lines))]
    bands: list[tuple[int, int]] = []
    max_r = _max_row_index(lines)
    for i, tr in enumerate(title_rows):
        end = title_rows[i + 1] - 1 if i + 1 < len(title_rows) else max_r
        bands.append((tr, end))
    return bands


def build_index_pairs(lines: list[str], cells: dict[tuple[int, int], dict[str, Any]]) -> list[IndexPair]:
    pairs: list[IndexPair] = []
    bands = _row_bands(lines)
    all_hmerge: list[ScopeSegment] = []
    for line in lines:
        all_hmerge.extend(_parse_hmerge_segments_from_line(line))

    for band_start, band_end in bands:
        major = [s for s in all_hmerge if s.header_row == band_start and _is_major_title(s)]
        minor: list[ScopeSegment] = []
        for s in all_hmerge:
            if band_start < s.header_row < band_end and not _is_major_title(s):
                minor.append(s)

        groups, data_start = _build_direction_groups_for_band(lines, cells, band_start, band_end)

        for ri in range(data_start, band_end + 1):
            for g in groups:
                idx_info = cells.get((ri, g.idx_col))
                thk_info = cells.get((ri, g.thk_col))
                if not idx_info or not thk_info:
                    continue
                idx_val = parse_int_cell(idx_info.get("text", ""))
                thk_val = parse_float_cell(thk_info.get("text", ""))
                if idx_val is None or thk_val is None:
                    continue
                scope = _scope_label_for_col(g.idx_col, major, minor)
                pairs.append(
                    IndexPair(
                        scope_label=scope,
                        direction=g.direction,
                        row_ri=ri,
                        idx_col=g.idx_col,
                        thk_col=g.thk_col,
                        index_val=abs(idx_val),
                        thickness=thk_val,
                        defect_by_color=bool(thk_info.get("defect_by_color")),
                    )
                )
    return pairs


def build_combo_cells(lines: list[str], cells: dict[tuple[int, int], dict[str, Any]]) -> list[ComboIndexCell]:
    """扫描编号列上的组合编号单元格（与 build_index_pairs 列组/作用域一致）。"""
    out: list[ComboIndexCell] = []
    bands = _row_bands(lines)
    all_hmerge: list[ScopeSegment] = []
    for line in lines:
        all_hmerge.extend(_parse_hmerge_segments_from_line(line))

    for band_start, band_end in bands:
        major = [s for s in all_hmerge if s.header_row == band_start and _is_major_title(s)]
        minor: list[ScopeSegment] = []
        for s in all_hmerge:
            if band_start < s.header_row < band_end and not _is_major_title(s):
                minor.append(s)

        groups, data_start = _build_direction_groups_for_band(lines, cells, band_start, band_end)

        for ri in range(data_start, band_end + 1):
            for g in groups:
                idx_info = cells.get((ri, g.idx_col))
                if not idx_info:
                    continue
                raw = (idx_info.get("text") or "").strip()
                combo = parse_combo_cell(raw)
                if not combo:
                    continue
                row_part, tube_part = combo
                thk_info = cells.get((ri, g.thk_col))
                thk_val = parse_float_cell(thk_info.get("text", "")) if thk_info else None
                scope = _scope_label_for_col(g.idx_col, major, minor)
                out.append(
                    ComboIndexCell(
                        scope_label=scope,
                        direction=g.direction,
                        row_ri=ri,
                        idx_col=g.idx_col,
                        thk_col=g.thk_col,
                        raw=raw,
                        row_part=row_part,
                        tube_part=tube_part,
                        thickness=thk_val,
                    )
                )
    return out


def parse_tables_from_chunk(chunk: str) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    current: list[str] = []
    for line in (chunk or "").splitlines():
        if line.strip().startswith(TABLE_MARK):
            if current:
                tables.append(_finalize_table(current))
            current = [line]
            continue
        if current:
            if line.strip().startswith(TABLE_MARK) and len(current) > 1:
                tables.append(_finalize_table(current))
                current = [line]
            else:
                current.append(line)
    if current:
        tables.append(_finalize_table(current))
    return tables


def _finalize_table(lines: list[str]) -> ParsedTable:
    cells = parse_table_rows(lines)
    groups = parse_column_groups(lines)
    pairs = build_index_pairs(lines, cells)
    combo_cells = build_combo_cells(lines, cells)
    return ParsedTable(lines=lines, cells=cells, pairs=pairs, combo_cells=combo_cells, groups=groups)
