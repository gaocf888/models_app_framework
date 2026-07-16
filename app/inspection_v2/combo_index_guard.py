"""
DOCX V2 分块：按 chunk 编号列组合编号（如 2-1）校正行号/管号并标记跳过设备语义规则。

parse 阶段基于 [DOCX_V2_TABLE] 网格判定；落盘 record 含内部字段 combo_index_from_chunk。
"""

from __future__ import annotations

import re
from typing import Any

from app.inspection_v2.docx_v2_table_parse import (
    TABLE_MARK,
    ComboIndexCell,
    parse_combo_cell,
    parse_tables_from_chunk,
    thk_close,
)
from app.inspection_v2.record_normalization import COMBO_INDEX_FROM_CHUNK
from app.inspection_v2.tube_thickness_bind_guard import _location_matches

_COMBO_GUARD_PREFIX = "combo_index_guard:"


def _record_location(rec: dict[str, Any]) -> str:
    return str(rec.get("检测位置") or rec.get("location") or "").strip()


def _record_thickness(rec: dict[str, Any]) -> float | None:
    raw = rec.get("壁厚") if rec.get("壁厚") not in (None, "") else rec.get("thickness")
    if isinstance(raw, (int, float)):
        return float(raw)
    m = re.search(r"-?\d+(?:\.\d+)?", str(raw or ""))
    return float(m.group(0)) if m else None


def _record_row_str(rec: dict[str, Any]) -> str:
    return str(rec.get("行号") or rec.get("row_no") or "").strip()


def _record_tube_str(rec: dict[str, Any]) -> str:
    return str(rec.get("管号") or rec.get("tube_no") or "").strip()


def _record_row_int(rec: dict[str, Any]) -> int | None:
    s = _record_row_str(rec)
    digits = re.sub(r"\D", "", s)
    return int(digits) if digits else None


def _record_tube_int(rec: dict[str, Any]) -> int | None:
    s = _record_tube_str(rec)
    if not re.fullmatch(r"-?\d+", s):
        return None
    return int(s)


def _cell_matches_record_parts(cell: ComboIndexCell, rec: dict[str, Any]) -> bool:
    row_s = _record_row_str(rec)
    tube_s = _record_tube_str(rec)
    from_row = parse_combo_cell(row_s)
    if from_row and from_row == (cell.row_part, cell.tube_part):
        return True
    from_tube = parse_combo_cell(tube_s)
    if from_tube and from_tube == (cell.row_part, cell.tube_part):
        return True
    row_i = _record_row_int(rec)
    tube_i = _record_tube_int(rec)
    if row_i is not None and str(row_i) == cell.row_part:
        if tube_i is None or str(abs(tube_i)) == cell.tube_part:
            return True
    if tube_i is not None and str(abs(tube_i)) == cell.tube_part:
        if row_i is None or str(row_i) == cell.row_part:
            return True
    return False


def _filter_combo_candidates(
    cells: list[ComboIndexCell],
    *,
    location: str,
    thickness: float | None,
) -> list[ComboIndexCell]:
    out: list[ComboIndexCell] = []
    for cell in cells:
        if not _location_matches(location, cell.scope_label):
            continue
        if thickness is not None:
            if cell.thickness is None or not thk_close(cell.thickness, thickness):
                continue
        out.append(cell)
    return out


def _pick_combo_cell(cells: list[ComboIndexCell], rec: dict[str, Any]) -> ComboIndexCell | None:
    if not cells:
        return None
    if len(cells) == 1:
        return cells[0]
    narrowed = [c for c in cells if _cell_matches_record_parts(c, rec)]
    if len(narrowed) == 1:
        return narrowed[0]
    # 多候选但行段+管段一致（同编号重复出现）时取首个，仍打保护标记
    if narrowed:
        keys = {(c.row_part, c.tube_part) for c in narrowed}
        if len(keys) == 1:
            return narrowed[0]
    return None


def _apply_combo_fix(item: dict[str, Any], cell: ComboIndexCell) -> None:
    item["行号"] = cell.row_part
    item["row_no"] = cell.row_part
    item["管号"] = cell.tube_part
    item["tube_no"] = cell.tube_part
    item[COMBO_INDEX_FROM_CHUNK] = True
    msg = f"{_COMBO_GUARD_PREFIX}chunk_cell={cell.raw}(r{cell.row_ri} c{cell.idx_col})"
    warns = item.get("warnings")
    warn_list = [str(x) for x in warns] if isinstance(warns, list) else []
    if msg not in warn_list:
        warn_list.append(msg)
    item["warnings"] = warn_list


def apply_docx_v2_combo_index_guard(
    records: list[dict[str, Any]],
    chunk: str,
) -> list[dict[str, Any]]:
    if not records or TABLE_MARK not in (chunk or ""):
        return records
    tables = parse_tables_from_chunk(chunk)
    all_combo: list[ComboIndexCell] = []
    for tbl in tables:
        all_combo.extend(tbl.combo_cells)
    if not all_combo:
        return records

    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            out.append(rec)
            continue
        item = dict(rec)
        if item.get(COMBO_INDEX_FROM_CHUNK):
            out.append(item)
            continue
        location = _record_location(item)
        thickness = _record_thickness(item)
        candidates = _filter_combo_candidates(all_combo, location=location, thickness=thickness)
        picked = _pick_combo_cell(candidates, item)
        if picked is not None:
            _apply_combo_fix(item, picked)
        out.append(item)
    return out
