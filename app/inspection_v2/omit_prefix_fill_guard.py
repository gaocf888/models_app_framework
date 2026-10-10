"""
DOCX V2：省略前缀表列组扫描补洞（组顶纯数字继承前缀）。

仅在调用方确认 prompt_version=v2 时接入；本模块内再门控：
块内须出现完整形态编号（如 D14下2 / B12 下 1 / 前上1），否则原样返回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.inspection_v2.docx_v2_table_parse import (
    TABLE_MARK,
    _build_direction_groups_for_band,
    _row_bands,
    parse_float_cell,
    parse_table_rows,
    parse_tables_from_chunk,
    thk_close,
)
from app.inspection_v2.record_normalization import pick_record_location, sync_location_aliases

logger = get_logger(__name__)

_OMIT_COMPLETE_RE = re.compile(
    r"^(?P<tag>[A-Za-z]+\d*|[前后左右])\s*(?P<dir>[上下])\s*(?P<seq>\d+)$"
)
_PURE_INT_RE = re.compile(r"^-?\d+$")
_WARNING_PREFIX = "omit_prefix_fill_guard:"


@dataclass(frozen=True)
class _ExpectedPoint:
    prefix: str  # 如 D14下
    direction: str  # 上 | 下
    tube_abs: int
    thickness: float
    from_pure_digit: bool
    defect_by_color: bool
    group_idx: int
    row_ri: int


def chunk_has_omit_prefix_form(chunk: str) -> bool:
    """块内任一单元格为省略前缀完整形态。"""
    if TABLE_MARK not in (chunk or ""):
        return False
    for ln in (chunk or "").splitlines():
        for m in re.finditer(r"='([^']*)'", ln):
            if _parse_omit_complete(m.group(1)):
                return True
    return False


def _parse_omit_complete(text: str) -> tuple[str, str, int] | None:
    m = _OMIT_COMPLETE_RE.fullmatch((text or "").strip())
    if not m:
        return None
    tag = m.group("tag")
    direction = m.group("dir")
    seq = int(m.group("seq"))
    return f"{tag}{direction}", direction, seq


def _extract_prelude(chunk: str) -> str:
    parts: list[str] = []
    for ln in (chunk or "").splitlines():
        st = ln.strip()
        if st.startswith(TABLE_MARK):
            break
        if not st or st.startswith("[处理单元"):
            continue
        parts.append(st)
    return "".join(parts).strip()


def _strip_prelude_process_suffix(prelude: str) -> str:
    t = (prelude or "").strip()
    if not t:
        return ""
    for sep in ("规格", "材质", "测厚记录", "测厚以", "以吹灰器为中心", "测厚"):
        idx = t.find(sep)
        if idx > 0:
            t = t[:idx]
    return t.strip()


def compose_heated_surface(prelude: str, prefix: str) -> str:
    """
    表前部位 + 前缀位号/方向 → 受热面。
    例：水冷壁D层吹灰器… + D14下 → 水冷壁D14吹灰器下
    """
    direction = ""
    tag = prefix
    dm = re.fullmatch(r"(.+?)([上下])", (prefix or "").strip())
    if dm:
        tag, direction = dm.group(1), dm.group(2)

    base = _strip_prelude_process_suffix(prelude)
    if not base:
        return f"{tag}{direction}" if direction else tag

    out = base
    if re.search(r"[A-Za-z前后左右]\d*层", out):
        out = re.sub(r"[A-Za-z前后左右]\d*层", tag, out, count=1)
    elif tag and tag not in out:
        out = out.rstrip("上下") + tag

    if direction:
        out = re.sub(r"[上下]\s*$", "", out) + direction
    return out


def _record_thickness(rec: dict[str, Any]) -> float | None:
    raw = rec.get("壁厚") if rec.get("壁厚") not in (None, "") else rec.get("thickness")
    if isinstance(raw, (int, float)):
        return float(raw)
    return parse_float_cell(str(raw or ""))


def _record_tube_abs(rec: dict[str, Any]) -> int | None:
    raw = rec.get("管号") if rec.get("管号") not in (None, "") else rec.get("tube_no")
    s = str(raw or "").strip()
    if not re.fullmatch(r"-?\d+", s):
        return None
    return abs(int(s))


def _append_warning(rec: dict[str, Any], msg: str) -> None:
    warns = rec.get("warnings")
    if not isinstance(warns, list):
        warns = []
    if msg not in warns:
        warns.append(msg)
    rec["warnings"] = warns


def _iter_expected_points(chunk: str) -> list[_ExpectedPoint]:
    out: list[_ExpectedPoint] = []
    for tbl in parse_tables_from_chunk(chunk):
        lines = tbl.lines
        cells = tbl.cells or parse_table_rows(lines)
        for band_start, band_end in _row_bands(lines):
            groups, data_start, _ = _build_direction_groups_for_band(
                lines, cells, band_start, band_end
            )
            if not groups:
                continue
            current_prefix = ""
            current_dir = "下"
            for gi, g in enumerate(groups):
                for ri in range(max(data_start, band_start), band_end + 1):
                    idx_info = cells.get((ri, g.idx_col)) or {}
                    thk_info = cells.get((ri, g.thk_col)) or {}
                    id_text = str(idx_info.get("text") or "").strip()
                    thk_text = str(thk_info.get("text") or "").strip()
                    if not id_text and not thk_text:
                        continue
                    thk_val = parse_float_cell(thk_text) if thk_text else None
                    complete = _parse_omit_complete(id_text)
                    if complete:
                        prefix, direction, seq = complete
                        current_prefix = prefix
                        current_dir = direction
                        if thk_val is None:
                            continue
                        out.append(
                            _ExpectedPoint(
                                prefix=prefix,
                                direction=direction,
                                tube_abs=seq,
                                thickness=thk_val,
                                from_pure_digit=False,
                                defect_by_color=bool(thk_info.get("defect_by_color")),
                                group_idx=gi,
                                row_ri=ri,
                            )
                        )
                        continue
                    if (
                        _PURE_INT_RE.fullmatch(id_text)
                        and thk_val is not None
                        and current_prefix
                    ):
                        tube_abs = abs(int(id_text))
                        out.append(
                            _ExpectedPoint(
                                prefix=current_prefix,
                                direction=current_dir,
                                tube_abs=tube_abs,
                                thickness=thk_val,
                                from_pure_digit=True,
                                defect_by_color=bool(thk_info.get("defect_by_color")),
                                group_idx=gi,
                                row_ri=ri,
                            )
                        )
    return out


def _apply_point_to_record(
    rec: dict[str, Any],
    point: _ExpectedPoint,
    *,
    location: str,
    filled: bool,
) -> None:
    tube_signed = -point.tube_abs if point.direction == "上" else point.tube_abs
    rec["管号"] = str(tube_signed)
    rec["tube_no"] = str(tube_signed)
    rec["行号"] = str(rec.get("行号") or rec.get("row_no") or "1") or "1"
    rec["row_no"] = rec["行号"]
    rec["壁厚"] = point.thickness
    rec["thickness"] = point.thickness
    if "受热面" in rec or filled:
        rec["受热面"] = location
    sync_location_aliases(rec, location)
    if not str(rec.get("检测类型") or rec.get("detection_type") or "").strip():
        rec["检测类型"] = "缺陷" if point.defect_by_color else "测厚"
        rec["detection_type"] = rec["检测类型"]
    if rec.get("缺陷类型") is None and "缺陷类型" not in rec:
        rec["缺陷类型"] = ""
    if not str(rec.get("是否换管") or "").strip():
        det = str(rec.get("检测类型") or "").strip()
        rec["是否换管"] = "是" if det == "缺陷" else "否"
    msg = (
        f"{_WARNING_PREFIX}filled prefix={point.prefix} tube={tube_signed}"
        if filled
        else f"{_WARNING_PREFIX}fixed prefix={point.prefix} tube={tube_signed}"
    )
    _append_warning(rec, msg)


def apply_docx_v2_omit_prefix_fill_guard(
    records: list[dict[str, Any]],
    chunk: str,
) -> list[dict[str, Any]]:
    """
    按列组扫描省略前缀表：校正/补全纯数字续写点（含组顶）。
    无完整形态或非 DOCX_V2 表 → 原样返回。
    """
    if TABLE_MARK not in (chunk or ""):
        return records
    if not chunk_has_omit_prefix_form(chunk):
        return records

    expected = _iter_expected_points(chunk)
    if not expected:
        return records

    prelude = _extract_prelude(chunk)
    working = [dict(r) if isinstance(r, dict) else r for r in (records or [])]
    pool: list[dict[str, Any]] = [r for r in working if isinstance(r, dict)]
    used: set[int] = set()
    result: list[dict[str, Any]] = []
    fill_count = 0
    fix_count = 0

    for point in expected:
        location = compose_heated_surface(prelude, point.prefix)
        tag = point.prefix  # D14下
        best_i: int | None = None
        best_score = -1
        for i, rec in enumerate(pool):
            if i in used:
                continue
            tube_a = _record_tube_abs(rec)
            thk = _record_thickness(rec)
            if tube_a is None or thk is None:
                continue
            if tube_a != point.tube_abs or not thk_close(thk, point.thickness):
                continue
            loc = pick_record_location(rec)
            score = 1
            if tag and tag[:2] in loc:  # D1 / B1 rough
                score += 2
            if tag and (tag in loc or tag.rstrip("上下") in loc):
                score += 5
            if score > best_score:
                best_score = score
                best_i = i
        if best_i is not None:
            rec = pool[best_i]
            used.add(best_i)
            before_loc = pick_record_location(rec)
            before_tube = str(rec.get("管号") or rec.get("tube_no") or "")
            _apply_point_to_record(rec, point, location=location, filled=False)
            if before_loc != location or before_tube != str(rec.get("管号")):
                fix_count += 1
            result.append(rec)
            continue

        if not point.from_pure_digit:
            # 完整形态点：LLM 未给出则不强制新建（避免重复臆造）
            continue

        new_rec: dict[str, Any] = {
            "受热面": location,
            "行号": "1",
            "管号": "",
            "壁厚": point.thickness,
            "检测类型": "缺陷" if point.defect_by_color else "测厚",
            "缺陷类型": "",
            "是否换管": "是" if point.defect_by_color else "否",
            "warnings": [],
        }
        _apply_point_to_record(new_rec, point, location=location, filled=True)
        fill_count += 1
        result.append(new_rec)

    # 保留未匹配到的原 records（顺序：扫描补齐结果 + 剩余）
    leftovers = [pool[i] for i in range(len(pool)) if i not in used]
    merged = result + leftovers

    if fill_count or fix_count:
        logger.info(
            "inspection_extract omit_prefix_fill_guard filled=%s fixed=%s expected=%s out=%s",
            fill_count,
            fix_count,
            len(expected),
            len(merged),
        )
    return merged
