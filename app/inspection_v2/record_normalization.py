"""
检修记录确定性规范化（与 configs/prompts.yaml · inspection_extract_parse 对齐）。

- 管号符号：表格语境含「上/向上/上数…」时对纯整数管号补负号；含「下/向下…」时去掉多余负号。
- 设备语义：水冷壁/包墙/后竖井/冷灰斗 → 行号固定为 1；再热器/过热器/省煤器等 → 管号固定为 1。
- 组合编号（如 2-1）：combo_index_guard 基于 chunk 编号列判定；无 chunk 时字段字面量兜底。
"""

from __future__ import annotations

import re
from typing import Any

_UP_MARKERS = ("向上", "上数", "上排", "上行", "上测", "上部")
_DOWN_MARKERS = ("向下", "下数", "下排", "下行", "下测", "下部")

# 检测位置含下列词时：行号必须为 1，表格编号列语义为管号
_WALL_ROW1_MARKERS = ("水冷壁", "包墙", "后竖井", "冷灰斗")
# 检测位置含下列词时：管号必须为 1，表格编号列语义为行号
_REHEATER_TUBE1_MARKERS = ("再热器", "低再", "高再", "过热器", "低过", "高过", "省煤器")

# parse 阶段 combo_index_guard 写入；落盘供 post_process / GET chunks 重放（公开 API 需 strip）
COMBO_INDEX_FROM_CHUNK = "combo_index_from_chunk"
_COMBO_GUARD_PREFIX = "combo_index_guard:"


def _collapse_ws(s: str) -> str:
    return " ".join((s or "").split())


def _is_pure_int(s: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", (s or "").strip()))


_COMBO_INDEX_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


def _parse_combo_index(s: str) -> tuple[str, str] | None:
    m = _COMBO_INDEX_RE.fullmatch((s or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def _resolve_combo_row_tube(row: str, tube: str) -> tuple[str, str, bool]:
    """组合编号 2-1 → (行号=2, 管号=1)；行号字段优先于管号字段。"""
    from_row = _parse_combo_index(row)
    if from_row:
        return from_row[0], from_row[1], True
    from_tube = _parse_combo_index(tube)
    if from_tube:
        return from_tube[0], from_tube[1], True
    return row, tube, False


def is_combo_index_protected(item: dict[str, Any]) -> bool:
    """record 已由 chunk 组合编号守卫标记，跳过设备语义与管号符号校正。"""
    if item.get(COMBO_INDEX_FROM_CHUNK):
        return True
    warns = item.get("warnings")
    if isinstance(warns, list):
        return any(str(w).startswith(_COMBO_GUARD_PREFIX) for w in warns)
    return False


def _sync_row_tube_fields(out: dict[str, Any]) -> None:
    loc = str(out.get("检测位置") or out.get("location") or "").strip()
    row = str(out.get("行号") or out.get("row_no") or "").strip()
    tube = str(out.get("管号") or out.get("tube_no") or "").strip()
    out["检测位置"] = loc
    out["location"] = loc
    out["行号"] = row
    out["row_no"] = row
    out["管号"] = tube
    out["tube_no"] = tube


def _loc_has(loc: str, markers: tuple[str, ...]) -> bool:
    return any(m in (loc or "") for m in markers)


def _digits_only(s: str) -> str:
    digits = re.sub(r"\D", "", s or "")
    return digits


def normalize_device_row_tube_by_location(
    location: str,
    row_no: str,
    tube_no: str,
) -> tuple[str, str, str, list[str]]:
    """
    按检测位置设备类型校正行号/管号（与 parse 提示词 §行号与管号 一致）。

    - 水冷壁系：行号 → "1"；若 LLM 将编号误写入行号且管号为 1/空，迁到管号。
    - 再热器/过热器/省煤器系：管号 → "1"；行号去字母；若编号误写入管号且行号为 1/空，迁到行号。
    - 组合编号（如 2-1）：跳过本函数内全部设备语义校正。
    """
    warns: list[str] = []
    loc = _collapse_ws(location)
    row = _collapse_ws(row_no)
    tube = (tube_no or "").strip()

    row, tube, is_combo = _resolve_combo_row_tube(row, tube)
    if is_combo:
        warns.append("deterministic_combo_index:跳过设备语义校正")
        return loc, row, tube, warns

    if _loc_has(loc, _WALL_ROW1_MARKERS):
        if row != "1":
            if _is_pure_int(row) and (not tube or tube in ("0", "1")):
                tube = row
                warns.append("deterministic_row_tube:水冷壁系编号在行号→已迁至管号")
            row = "1"
            warns.append("deterministic_row_fix:水冷壁系行号→1")

    if _loc_has(loc, _REHEATER_TUBE1_MARKERS):
        digits = _digits_only(row)
        if digits and row != digits:
            row = digits
            warns.append("deterministic_row_digits:过热器系行号去字母")
        if tube != "1":
            if _is_pure_int(tube) and row in ("", "1", "0"):
                row = tube
                warns.append("deterministic_row_tube:过热器系编号在管号→已迁至行号")
            tube = "1"
            warns.append("deterministic_tube_fix:过热器系管号→1")

    return loc, row, tube, warns


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

    row, tube, is_combo = _resolve_combo_row_tube(row, tube)
    if is_combo:
        warns.append("deterministic_combo_index:跳过设备语义校正")
        return loc, row, tube, warns

    loc, row, tube, dev_warns = normalize_device_row_tube_by_location(loc, row, tube)
    warns.extend(dev_warns)

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
    if is_combo_index_protected(out):
        _sync_row_tube_fields(out)
        return out

    loc = str(out.get("检测位置") or out.get("location") or "").strip()
    row = str(out.get("行号") or out.get("row_no") or "").strip()
    tube = str(out.get("管号") or out.get("tube_no") or "").strip()
    ev = str(out.get("evidence") or out.get("证据") or "").strip()

    nloc, nrow, ntube, warns = normalize_location_row_tube(loc, row, tube, evidence=ev)
    # 始终写入中英字段，避免仅有 row_no/location 时 API 仍返回未修正的「行号」
    out["检测位置"] = nloc
    out["location"] = nloc
    out["行号"] = nrow
    out["row_no"] = nrow
    out["管号"] = ntube
    out["tube_no"] = ntube

    if warns:
        w = out.get("warnings")
        base = [str(x) for x in w] if isinstance(w, list) else []
        for msg in warns:
            if msg not in base:
                base.append(msg)
        out["warnings"] = base
    return out
