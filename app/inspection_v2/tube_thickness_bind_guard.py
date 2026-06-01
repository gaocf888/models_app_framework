"""
DOCX V2 分块：Parse 后按单元格网格校正「管号+壁厚」绑定（方案 C）。

在块内建立 (检测位置作用域, 方向, 编号, 壁厚) 索引；唯一匹配时纠正 LLM 错绑的管号。
"""

from __future__ import annotations

import re
from typing import Any

from app.inspection_v2.docx_v2_table_parse import (
    TABLE_MARK,
    IndexPair,
    parse_tables_from_chunk,
    thk_close,
)
from app.inspection_v2.record_normalization import (
    _REHEATER_TUBE1_MARKERS,
    _WALL_ROW1_MARKERS,
)

_REHEATER_LOC = _REHEATER_TUBE1_MARKERS
_WALL_LOC = _WALL_ROW1_MARKERS


def _record_location(rec: dict[str, Any]) -> str:
    return str(rec.get("检测位置") or rec.get("location") or "").strip()


def _record_thickness(rec: dict[str, Any]) -> float | None:
    raw = rec.get("壁厚") if rec.get("壁厚") not in (None, "") else rec.get("thickness")
    if isinstance(raw, (int, float)):
        return float(raw)
    m = re.search(r"-?\d+(?:\.\d+)?", str(raw or ""))
    return float(m.group(0)) if m else None


def _record_tube_int(rec: dict[str, Any]) -> int | None:
    raw = rec.get("管号") if rec.get("管号") not in (None, "") else rec.get("tube_no")
    s = str(raw or "").strip()
    if not re.fullmatch(r"-?\d+", s):
        return None
    return int(s)


def _record_row_int(rec: dict[str, Any]) -> int | None:
    raw = rec.get("行号") if rec.get("行号") not in (None, "") else rec.get("row_no")
    s = str(raw or "").strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    return int(digits)


def _tube_direction(tube_n: int | None) -> str:
    if tube_n is not None and tube_n < 0:
        return "上"
    return "下"


def _loc_has(loc: str, markers: tuple[str, ...]) -> bool:
    return any(m in (loc or "") for m in markers)


def _device_mode(location: str) -> str:
    if _loc_has(location, _REHEATER_LOC):
        return "reheater"
    if _loc_has(location, _WALL_LOC):
        return "wall"
    return "generic"


def _wall_side(text: str) -> str | None:
    if "左墙" in text or re.search(r"左墙\d", text):
        return "左"
    if "右墙" in text or re.search(r"右墙\d", text):
        return "右"
    return None


def _wind_hole_no(text: str) -> str | None:
    m = re.search(r"第(\d+)贴壁风孔", text)
    return m.group(1) if m else None


def _sub_header_key(text: str) -> str | None:
    m = re.search(r"(左墙|右墙)\d+-\d+", text)
    return m.group(0) if m else None


def _location_matches(record_loc: str, scope_label: str) -> bool:
    if not scope_label:
        return True
    loc = (record_loc or "").strip()
    if not loc:
        return False
    if scope_label in loc or loc in scope_label:
        return True

    loc_side = _wall_side(loc)
    scope_side = _wall_side(scope_label)
    if loc_side and scope_side and loc_side != scope_side:
        return False

    loc_hole = _wind_hole_no(loc)
    scope_hole = _wind_hole_no(scope_label)
    if loc_hole and scope_hole and loc_hole != scope_hole:
        return False

    loc_sub = _sub_header_key(loc)
    scope_sub = _sub_header_key(scope_label)
    if loc_sub and scope_sub and loc_sub != scope_sub:
        return False

    # 子表头「左墙1-1」与长表头「水冷壁左墙…」：一侧有子表头时优先按子表头对齐
    if loc_sub and scope_sub is None:
        return loc_sub in scope_label
    if scope_sub and loc_sub is None:
        return scope_sub in loc

    # 弱匹配：共享足够长的位置片段（避免「水冷壁+第1层」误匹配左/右墙）
    for seg in (scope_label, loc):
        if len(seg) >= 6 and seg in loc:
            return True
    return False


def _filter_pairs(
    pairs: list[IndexPair],
    *,
    location: str,
    direction: str,
    thickness: float,
) -> list[IndexPair]:
    out = [
        p
        for p in pairs
        if p.direction == direction and thk_close(p.thickness, thickness)
    ]
    if not out:
        return out
    scoped = [p for p in out if _location_matches(location, p.scope_label)]
    if scoped:
        return scoped
    # 仅一条作用域时允许弱匹配
    scopes = {p.scope_label for p in out if p.scope_label}
    if len(scopes) <= 1:
        return out
    return []


def _format_tube(tube_abs: int, direction: str) -> str:
    if direction == "上":
        return str(-abs(tube_abs))
    return str(abs(tube_abs))


def _apply_tube_fix(item: dict[str, Any], new_tube: str, msg: str) -> None:
    item["管号"] = new_tube
    item["tube_no"] = new_tube
    warns = item.get("warnings")
    warn_list = [str(x) for x in warns] if isinstance(warns, list) else []
    if msg not in warn_list:
        warn_list.append(msg)
    item["warnings"] = warn_list


def _bind_wall_record(
    item: dict[str, Any],
    pairs: list[IndexPair],
) -> dict[str, Any]:
    location = _record_location(item)
    thk = _record_thickness(item)
    tube_n = _record_tube_int(item)
    if thk is None:
        return item

    scoped = [p for p in pairs if thk_close(p.thickness, thk) and _location_matches(location, p.scope_label)]
    if not scoped:
        scoped = [p for p in pairs if thk_close(p.thickness, thk)]
        scopes = {p.scope_label for p in scoped if p.scope_label}
        if len(scopes) > 1:
            scoped = []

    if not scoped:
        return item

    tube_abs = abs(tube_n) if tube_n is not None else None

    # 1) 块内已有 (编号, 壁厚, 作用域) 精确匹配 → 仅校正正负号（须与管号符号方向一致）
    if tube_abs is not None:
        exact = [p for p in scoped if p.index_val == tube_abs]
        if exact:
            want = "上" if tube_n is not None and tube_n < 0 else "下"
            same_dir = [p for p in exact if p.direction == want]
            if same_dir:
                picked = same_dir[0]
                expected = _format_tube(picked.index_val, picked.direction)
                current = str(item.get("管号") or item.get("tube_no") or "").strip()
                if current != expected:
                    _apply_tube_fix(
                        item,
                        expected,
                        f"bind_guard:管号符号→{expected}(r{picked.row_ri} c{picked.idx_col})",
                    )
                return item
            # 编号仅存在于另一方向（如 -3 误标，3 实际为下侧）→ 继续走唯一方向纠正

    # 2) 编号错但壁厚对：在作用域内按方向唯一则纠正（如 上侧应为 2 却写成 3）
    up = [p for p in scoped if p.direction == "上"]
    down = [p for p in scoped if p.direction == "下"]
    up_vals = sorted({p.index_val for p in up})
    down_vals = sorted({p.index_val for p in down})

    if tube_n is not None and tube_n < 0 and len(up_vals) == 1:
        expected = _format_tube(up_vals[0], "上")
        if str(item.get("管号") or item.get("tube_no") or "").strip() != expected:
            _apply_tube_fix(
                item,
                expected,
                f"bind_guard:上侧管号+壁厚→{expected}",
            )
        return item

    if (tube_n is None or tube_n >= 0) and len(down_vals) == 1:
        if len(up_vals) == 1 and tube_abs == up_vals[0] and tube_n is not None and tube_n >= 0:
            expected = _format_tube(up_vals[0], "上")
            _apply_tube_fix(item, expected, f"bind_guard:上侧管号+壁厚→{expected}")
            return item
        expected = _format_tube(down_vals[0], "下")
        if tube_abs != down_vals[0]:
            _apply_tube_fix(
                item,
                expected,
                f"bind_guard:下侧管号+壁厚→{expected}",
            )
        return item

    if tube_n is not None and tube_n < 0 and len(up_vals) == 1 and tube_abs != up_vals[0]:
        expected = _format_tube(up_vals[0], "上")
        _apply_tube_fix(item, expected, f"bind_guard:上侧管号+壁厚→{expected}")
        return item

    if len(up_vals) > 1 or len(down_vals) > 1:
        warns = item.get("warnings")
        warn_list = [str(x) for x in warns] if isinstance(warns, list) else []
        msg = "bind_guard:ambiguous_thickness_in_scope"
        if msg not in warn_list:
            warn_list.append(msg)
        item["warnings"] = warn_list
    return item


def _bind_reheater_record(
    item: dict[str, Any],
    pairs: list[IndexPair],
) -> dict[str, Any]:
    """过热器系：网格编号列语义为行号，管号应为 1。"""
    location = _record_location(item)
    thk = _record_thickness(item)
    row_n = _record_row_int(item)
    if thk is None:
        return item
    direction = "下"
    candidates = _filter_pairs(pairs, location=location, direction=direction, thickness=thk)
    if not candidates and pairs:
        candidates = [p for p in pairs if thk_close(p.thickness, thk) and _location_matches(location, p.scope_label)]
    if not candidates:
        return item

    if row_n is not None:
        exact = [p for p in candidates if p.index_val == row_n]
        if exact:
            return item

    index_vals = sorted({p.index_val for p in candidates})
    if len(index_vals) == 1:
        new_row = str(index_vals[0])
        if str(item.get("行号") or item.get("row_no") or "").strip() != new_row:
            item["行号"] = new_row
            item["row_no"] = new_row
            warns = item.get("warnings")
            warn_list = [str(x) for x in warns] if isinstance(warns, list) else []
            msg = f"bind_guard:行号+壁厚绑定→{new_row}"
            if msg not in warn_list:
                warn_list.append(msg)
            item["warnings"] = warn_list
        return item

    return item


def _bind_generic_record(
    item: dict[str, Any],
    pairs: list[IndexPair],
) -> dict[str, Any]:
    location = _record_location(item)
    thk = _record_thickness(item)
    tube_n = _record_tube_int(item)
    if thk is None or tube_n is None:
        return item
    direction = _tube_direction(tube_n)
    candidates = _filter_pairs(pairs, location=location, direction=direction, thickness=thk)
    if not candidates:
        return item
    tube_abs = abs(tube_n)
    if any(p.index_val == tube_abs for p in candidates):
        return item
    index_vals = sorted({p.index_val for p in candidates})
    if len(index_vals) == 1:
        new_tube = _format_tube(index_vals[0], direction)
        _apply_tube_fix(item, new_tube, f"bind_guard:generic管号+壁厚→{new_tube}")
    return item


def apply_docx_v2_tube_thickness_bind_guard(
    records: list[dict[str, Any]],
    chunk: str,
) -> list[dict[str, Any]]:
    if not records or TABLE_MARK not in (chunk or ""):
        return records
    tables = parse_tables_from_chunk(chunk)
    all_pairs: list[IndexPair] = []
    for tbl in tables:
        all_pairs.extend(tbl.pairs)
    if not all_pairs:
        return records

    out: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            out.append(rec)
            continue
        item = dict(rec)
        mode = _device_mode(_record_location(item))
        if mode == "wall":
            item = _bind_wall_record(item, all_pairs)
        elif mode == "reheater":
            item = _bind_reheater_record(item, all_pairs)
        else:
            item = _bind_generic_record(item, all_pairs)
        out.append(item)
    return out
