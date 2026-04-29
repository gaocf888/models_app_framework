"""
检修记录确定性规范化（与 configs/prompts.yaml · inspection_extract_parse 对齐）。

管号符号：表格语境含「上/向上/上数…」时对纯整数管号补负号；含「下/向下…」时去掉多余负号。
不改动「2-1」「5-2」等非纯整数形式。
"""

from __future__ import annotations

import re
from typing import Any

_UP_MARKERS = ("向上", "上数", "上排", "上行", "上测", "上部")
_DOWN_MARKERS = ("向下", "下数", "下排", "下行", "下测", "下部")


def _collapse_ws(s: str) -> str:
    return " ".join((s or "").split())


def _has_up_context(row_no: str, location: str, evidence: str) -> bool:
    s = f"{row_no}{location}{evidence}"
    if any(m in s for m in _UP_MARKERS):
        return True
    r = (row_no or "").strip()
    return bool(r.startswith("上"))


def _has_down_context(row_no: str, location: str, evidence: str) -> bool:
    s = f"{row_no}{location}{evidence}"
    if any(m in s for m in _DOWN_MARKERS):
        return True
    r = (row_no or "").strip()
    return bool(r.startswith("下"))


def normalize_location_row_tube(
    location: str,
    row_no: str,
    tube_no: str,
    *,
    evidence: str = "",
) -> tuple[str, str, str, list[str]]:
    warns: list[str] = []
    loc = _collapse_ws(location)
    row = _collapse_ws(row_no)
    tube = (tube_no or "").strip()

    int_only = re.fullmatch(r"-?\d+", tube)
    if not int_only:
        return loc, row, tube, warns

    up = _has_up_context(row, loc, evidence)
    down = _has_down_context(row, loc, evidence)
    if up and down:
        warns.append("deterministic_tube_sign_skipped:上下并存")
        return loc, row, tube, warns

    n = int(tube)
    if up and n > 0 and not tube.startswith("-"):
        tube = str(-abs(n))
        warns.append("deterministic_tube_sign_applied:上→负号")
    elif down and n < 0:
        tube = str(abs(n))
        warns.append("deterministic_tube_sign_applied:下→去负号")

    return loc, row, tube, warns


def apply_deterministic_rules_to_record(item: dict[str, Any]) -> dict[str, Any]:
    """对单条原始 dict（中英字段混用）做规范化，供 canonicalize 前调用。"""
    out = dict(item)
    loc = str(out.get("检测位置") or out.get("location") or "").strip()
    row = str(out.get("行号") or out.get("row_no") or "").strip()
    tube = str(out.get("管号") or out.get("tube_no") or "").strip()
    ev = str(out.get("evidence") or out.get("证据") or "").strip()

    nloc, nrow, ntube, warns = normalize_location_row_tube(loc, row, tube, evidence=ev)
    if "检测位置" in out:
        out["检测位置"] = nloc
    if "location" in out:
        out["location"] = nloc
    if "行号" in out:
        out["行号"] = nrow
    if "row_no" in out:
        out["row_no"] = nrow
    if "管号" in out:
        out["管号"] = ntube
    if "tube_no" in out:
        out["tube_no"] = ntube

    if warns:
        w = out.get("warnings")
        base = [str(x) for x in w] if isinstance(w, list) else []
        for msg in warns:
            if msg not in base:
                base.append(msg)
        out["warnings"] = base
    return out
