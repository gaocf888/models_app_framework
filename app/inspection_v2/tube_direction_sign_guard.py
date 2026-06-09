"""
DOCX V2 分块：按 chunk 列组 direction（上/下/上数/下数…）校正管号正负。

仅改符号，不改编号绝对值；与 record_normalization 设备语义、bind_guard 编号绑定分工。
"""

from __future__ import annotations

import re
from typing import Any

from app.inspection_v2.docx_v2_table_parse import (
    DIRECTION_SOURCE_DEFAULT_DOWN,
    DIRECTION_SOURCE_EXPLICIT,
    DIRECTION_SOURCE_FALLBACK_4COL,
    DIRECTION_SOURCE_LOCATION_SKIP,
    TABLE_MARK,
    IndexPair,
    parse_tables_from_chunk,
    thk_close,
)
from app.inspection_v2.record_normalization import (
    _REHEATER_TUBE1_MARKERS,
    is_combo_index_protected,
)
from app.inspection_v2.tube_thickness_bind_guard import _location_matches

_SIGN_GUARD_PREFIX = "direction_sign_guard:"


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


def _loc_has_reheater(location: str) -> bool:
    return any(m in (location or "") for m in _REHEATER_TUBE1_MARKERS)


def format_tube_by_direction(tube_abs: int, direction: str) -> str:
    if direction == "上":
        return str(-abs(tube_abs))
    return str(abs(tube_abs))


def _append_warning(item: dict[str, Any], msg: str) -> None:
    warns = item.get("warnings")
    warn_list = [str(x) for x in warns] if isinstance(warns, list) else []
    if msg not in warn_list:
        warn_list.append(msg)
    item["warnings"] = warn_list


def _apply_tube_sign(item: dict[str, Any], new_tube: str, msg: str) -> None:
    item["管号"] = new_tube
    item["tube_no"] = new_tube
    _append_warning(item, f"{_SIGN_GUARD_PREFIX}{msg}")


def _match_index_pairs(
    pairs: list[IndexPair],
    *,
    tube_abs: int,
    thk: float,
    location: str,
) -> list[IndexPair]:
    scoped = [
        p
        for p in pairs
        if p.index_val == tube_abs
        and thk_close(p.thickness, thk)
        and _location_matches(location, p.scope_label)
    ]
    if scoped:
        return scoped
    loose = [p for p in pairs if p.index_val == tube_abs and thk_close(p.thickness, thk)]
    if not loose:
        return []
    scopes = {p.scope_label for p in loose if p.scope_label}
    if len(scopes) > 1:
        return []
    return loose


def _resolve_unique_direction(candidates: list[IndexPair]) -> str | None:
    dirs = {p.direction for p in candidates}
    if len(dirs) != 1:
        return None
    return dirs.pop()


def _sign_source_policy(source: str, *, allow_fallback_4col: bool) -> str | None:
    """返回 'full' | 'strip_negative_only' | None（skip）。"""
    if source == DIRECTION_SOURCE_EXPLICIT:
        return "full"
    if source == DIRECTION_SOURCE_DEFAULT_DOWN:
        return "strip_negative_only"
    if source == DIRECTION_SOURCE_LOCATION_SKIP:
        return None
    if source == DIRECTION_SOURCE_FALLBACK_4COL:
        return "full" if allow_fallback_4col else None
    return None


def apply_docx_v2_tube_direction_sign_guard(
    records: list[dict[str, Any]],
    chunk: str,
    *,
    allow_fallback_4col: bool = False,
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
        if is_combo_index_protected(item):
            out.append(item)
            continue

        location = _record_location(item)
        tube_n = _record_tube_int(item)
        thk = _record_thickness(item)
        tube_s = str(item.get("管号") or item.get("tube_no") or "").strip()

        if tube_n is None or thk is None:
            out.append(item)
            continue

        if _loc_has_reheater(location) and tube_s == "1":
            out.append(item)
            continue

        candidates = _match_index_pairs(
            all_pairs, tube_abs=abs(tube_n), thk=thk, location=location
        )
        if not candidates:
            out.append(item)
            continue

        direction = _resolve_unique_direction(candidates)
        if direction is None:
            _append_warning(item, "ambiguous_direction")
            out.append(item)
            continue

        source = candidates[0].direction_source
        policy = _sign_source_policy(source, allow_fallback_4col=allow_fallback_4col)
        if policy is None:
            out.append(item)
            continue

        expected = format_tube_by_direction(abs(tube_n), direction)
        if tube_s == expected:
            out.append(item)
            continue

        if policy == "strip_negative_only":
            if tube_n >= 0:
                out.append(item)
                continue
            expected = str(abs(tube_n))

        if direction == "上":
            _apply_tube_sign(item, expected, f"上→负号(r{candidates[0].row_ri})")
        else:
            _apply_tube_sign(item, expected, f"下→去负号(r{candidates[0].row_ri})")
        out.append(item)

    return out
