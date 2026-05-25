"""
DOCX V2 分块：按单元格颜色标注校正检测类型（弥补 LLM 同行/跨列误标）。

仅当分块含 [DOCX_V2_TABLE] 且能匹配到壁厚单元格时生效。
"""

from __future__ import annotations

import re
from typing import Any

_TABLE_MARK = "[DOCX_V2_TABLE"
_ROW_RE = re.compile(r"^r(\d+):\s*(.+)$")
_COL_GROUP_RE = re.compile(
    r"(c(\d+))(?:-c(\d+))?='([^']*)'(?:\[hmerge×\d+\])?",
)
_CELL_RE = re.compile(
    r"(c(\d+))(?:-c(\d+))?='([^']*)'"
    r"(?:\[hmerge×\d+\])?"
    r"(?:\[颜色标注:([^\]]+)\])?"
    r"(?:\[超标候选[^]]*\])?",
)


def _cell_defect_by_markup(color_note: str | None, part: str) -> bool:
    if color_note and "高亮" in color_note:
        return True
    if "[超标候选" in part:
        return True
    if color_note and "底纹=" in color_note:
        return True
    return False


def _parse_column_groups(lines: list[str]) -> list[dict[str, Any]]:
    """从表头行解析列组：上/下 -> (idx_col, thk_col)。"""
    groups: list[dict[str, Any]] = []
    for line in lines:
        if not line.startswith("r"):
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        body = m.group(2)
        for gm in _COL_GROUP_RE.finditer(body):
            c0 = int(gm.group(2))
            c1 = int(gm.group(3)) if gm.group(3) else c0
            label = (gm.group(4) or "").strip()
            if label not in ("上", "下", "向上", "向下", "上数", "下数", "上部", "下部"):
                continue
            direction = "上" if label in ("上", "向上", "上数", "上部") else "下"
            if c1 - c0 >= 1:
                groups.append(
                    {
                        "direction": direction,
                        "idx_col": c0,
                        "thk_col": c1,
                    }
                )
    if not groups:
        return [{"direction": "上", "idx_col": 0, "thk_col": 1}, {"direction": "下", "idx_col": 2, "thk_col": 3}]
    return groups


def _parse_table_rows(lines: list[str]) -> dict[tuple[int, int], dict[str, Any]]:
    """(row_idx, col_idx) -> {text, defect_by_color}。"""
    cells: dict[tuple[int, int], dict[str, Any]] = {}
    for line in lines:
        m = _ROW_RE.match(line)
        if not m:
            continue
        ri = int(m.group(1))
        body = m.group(2)
        for part in body.split(" | "):
            part = part.strip()
            cm = _CELL_RE.search(part)
            if not cm:
                continue
            c0 = int(cm.group(2))
            c1 = int(cm.group(3)) if cm.group(3) else c0
            text = (cm.group(4) or "").strip()
            color_note = cm.group(5)
            defect = _cell_defect_by_markup(color_note, part)
            for ci in range(c0, c1 + 1):
                cells[(ri, ci)] = {"text": text, "defect_by_color": defect}
    return cells


def _parse_tables_from_chunk(chunk: str) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    current: list[str] = []
    for line in (chunk or "").splitlines():
        if line.strip().startswith(_TABLE_MARK):
            if current:
                tables.append({"lines": current})
            current = [line]
            continue
        if current:
            if line.strip().startswith("[DOCX_V2_TABLE") and len(current) > 1:
                tables.append({"lines": current})
                current = [line]
            else:
                current.append(line)
    if current:
        tables.append({"lines": current})
    out: list[dict[str, Any]] = []
    for t in tables:
        lines = t["lines"]
        groups = _parse_column_groups(lines)
        cells = _parse_table_rows(lines)
        out.append({"groups": groups, "cells": cells})
    return out


def _record_tube_thickness(rec: dict[str, Any]) -> tuple[int | None, float | None, str]:
    tube_raw = rec.get("管号") or rec.get("tube_no") or ""
    thk_raw = rec.get("壁厚") or rec.get("thickness")
    tube_s = str(tube_raw).strip()
    tube_n: int | None = None
    if re.fullmatch(r"-?\d+", tube_s):
        tube_n = int(tube_s)
    thk: float | None = None
    if isinstance(thk_raw, (int, float)):
        thk = float(thk_raw)
    else:
        m = re.search(r"-?\d+(?:\.\d+)?", str(thk_raw or ""))
        if m:
            thk = float(m.group(0))
    direction = "上" if tube_n is not None and tube_n < 0 else "下"
    return tube_n, thk, direction


def _find_thk_cell_defect(
    tables: list[dict[str, Any]],
    *,
    tube_n: int | None,
    thk: float | None,
    direction: str,
) -> bool | None:
    if tube_n is None or thk is None:
        return None
    key_tube = abs(tube_n)
    for tbl in tables:
        groups = [g for g in tbl["groups"] if g["direction"] == direction]
        if not groups:
            groups = tbl["groups"]
        cells = tbl["cells"]
        for g in groups:
            idx_col = int(g["idx_col"])
            thk_col = int(g["thk_col"])
            for (ri, ci), info in cells.items():
                if ci != idx_col:
                    continue
                txt = (info.get("text") or "").strip()
                if not re.fullmatch(r"-?\d+", txt):
                    continue
                if int(txt) != key_tube and abs(int(txt)) != key_tube:
                    continue
                thk_info = cells.get((ri, thk_col))
                if not thk_info:
                    continue
                thk_txt = (thk_info.get("text") or "").strip()
                thk_m = re.search(r"-?\d+(?:\.\d+)?", thk_txt)
                if not thk_m:
                    continue
                if abs(float(thk_m.group(0)) - thk) > 0.02:
                    continue
                return bool(thk_info.get("defect_by_color"))
    return None


def apply_docx_v2_color_guard(records: list[dict[str, Any]], chunk: str) -> list[dict[str, Any]]:
    if not records or _TABLE_MARK not in (chunk or ""):
        return records
    tables = _parse_tables_from_chunk(chunk)
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
