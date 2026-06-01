"""
DOCX V2 分块：按单元格颜色标注校正检测类型（弥补 LLM 同行/跨列误标）。

仅当分块含 [DOCX_V2_TABLE] 且能匹配到壁厚单元格时生效。
"""

from __future__ import annotations

import re
from typing import Any

from app.inspection_v2.docx_v2_table_parse import (
    TABLE_MARK,
    parse_float_cell,
    parse_int_cell,
    parse_tables_from_chunk,
    thk_close,
)


def _record_tube_thickness(rec: dict[str, Any]) -> tuple[int | None, float | None, str]:
    tube_raw = rec.get("管号") or rec.get("tube_no") or ""
    thk_raw = rec.get("壁厚") or rec.get("thickness")
    tube_s = str(tube_raw).strip()
    tube_n: int | None = parse_int_cell(tube_s)
    thk: float | None = None
    if isinstance(thk_raw, (int, float)):
        thk = float(thk_raw)
    else:
        thk = parse_float_cell(str(thk_raw or ""))
    direction = "上" if tube_n is not None and tube_n < 0 else "下"
    return tube_n, thk, direction


def _find_thk_cell_defect(
    tables: list,
    *,
    tube_n: int | None,
    thk: float | None,
    direction: str,
) -> bool | None:
    if tube_n is None or thk is None:
        return None
    key_tube = abs(tube_n)
    for tbl in tables:
        groups = [g for g in tbl.groups if g.direction == direction]
        if not groups:
            groups = tbl.groups
        cells = tbl.cells
        for g in groups:
            idx_col = int(g.idx_col)
            thk_col = int(g.thk_col)
            for (ri, ci), info in cells.items():
                if ci != idx_col:
                    continue
                txt = (info.get("text") or "").strip()
                if parse_int_cell(txt) != key_tube:
                    continue
                thk_info = cells.get((ri, thk_col))
                if not thk_info:
                    continue
                thk_val = parse_float_cell(thk_info.get("text", ""))
                if thk_val is None or not thk_close(thk_val, thk):
                    continue
                return bool(thk_info.get("defect_by_color"))
    return None


def apply_docx_v2_color_guard(records: list[dict[str, Any]], chunk: str) -> list[dict[str, Any]]:
    if not records or TABLE_MARK not in (chunk or ""):
        return records
    tables = parse_tables_from_chunk(chunk)
    if not tables:
        return records

    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            out.append(rec)
            continue
        item = dict(rec)
        det = str(item.get("检测类型") or item.get("detection_type") or "").strip()
        tube_n, thk, direction = _record_tube_thickness(item)
        defect_by_color = _find_thk_cell_defect(
            tables, tube_n=tube_n, thk=thk, direction=direction
        )
        if defect_by_color is None:
            out.append(item)
            continue

        warns = item.get("warnings")
        warn_list = [str(x) for x in warns] if isinstance(warns, list) else []

        if defect_by_color:
            if det in ("", "测厚", "正常", "测量"):
                item["检测类型"] = "缺陷"
                if "detection_type" in item:
                    item["detection_type"] = "缺陷"
                msg = "color_guard:壁厚单元格含高亮/超标候选→缺陷"
                if msg not in warn_list:
                    warn_list.append(msg)
        else:
            if det in ("缺陷", "异常"):
                item["检测类型"] = "测厚"
                if "detection_type" in item:
                    item["detection_type"] = "测厚"
                item["缺陷类型"] = ""
                if "defect_type" in item:
                    item["defect_type"] = ""
                item["是否换管"] = "否"
                if "replaced" in item:
                    item["replaced"] = "否"
                msg = "color_guard:壁厚单元格无高亮→测厚(纠正LLM同行误标)"
                if msg not in warn_list:
                    warn_list.append(msg)

        if warn_list:
            item["warnings"] = warn_list
        out.append(item)
    return out
