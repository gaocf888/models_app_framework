"""层位 0 切片与压缩层：读 endpoints 行 + 字典，调用 formulas 内核。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.analysis_agent.deterministics.coverage import (
    canonical_project,
    layer0_mark_keys,
    layers_by_canonical_project,
)
from app.analysis_agent.deterministics.formulas import consecutive_layer_pairs, period_delta
from app.core.logging import get_logger

logger = get_logger(__name__)


def _f(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _canon_row_project(row: dict[str, Any]) -> str | None:
    raw = str(row.get("project_name") or "").strip()
    if not raw:
        return None
    return canonical_project(raw, "fcb") or canonical_project(raw, "jyb") or raw


def slice_layer0(gathered: dict[str, list[dict[str, Any]]], **_: Any) -> list[dict[str, Any]]:
    """
    层位 0 地面沉降行。

    只保留官方 fcb 覆盖 ∩ 层位 0 代表标；缺初/末丢弃。
    禁止用 t_station 全表补站。
    """
    endpoints = list(gathered.get("q_fcb_endpoints") or [])
    allow = layer0_mark_keys()
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in endpoints:
        if not isinstance(row, dict):
            continue
        project = _canon_row_project(row)
        mark = str(row.get("station_name") or "").strip()
        if not project or not mark:
            continue
        if (project, mark) not in allow:
            continue
        start = _f(row.get("settle_start"))
        end = _f(row.get("settle_end"))
        if start is None or end is None:
            continue
        by_key[(project, mark)] = {
            "area": row.get("area"),
            "project_name": project,
            "station_name": mark,
            "delta_mm": period_delta(start, end),
            "settle_start": start,
            "settle_end": end,
            "lon": row.get("lon"),
            "lat": row.get("lat"),
        }
    rows = list(by_key.values())
    rows.sort(key=lambda r: (str(r.get("area") or ""), str(r.get("project_name") or "")))
    logger.info("slice_layer0 rows=%s allow=%s", len(rows), len(allow))
    return rows


def layer_compress(gathered: dict[str, list[dict[str, Any]]], **_: Any) -> list[dict[str, Any]]:
    """
    连续层间压缩。F11 无 2→3/3→4；F9/F10 无 3→4（字典无键即无对）。
    一侧 Δ 缺失 → 该对不输出（格空，不是 0）。
    """
    endpoints = list(gathered.get("q_fcb_endpoints") or [])
    layer_marks = layers_by_canonical_project()
    mark_to_layer: dict[tuple[str, str], int] = {}
    for project, layers in layer_marks.items():
        for layer, mark in layers.items():
            mark_to_layer[(project, mark)] = int(layer)

    grouped: dict[str, dict[str, Any]] = {}
    deltas: dict[str, dict[int, float]] = defaultdict(dict)
    mark_at: dict[str, dict[int, str]] = defaultdict(dict)

    for row in endpoints:
        if not isinstance(row, dict):
            continue
        project = _canon_row_project(row)
        mark = str(row.get("station_name") or "").strip()
        if not project or not mark:
            continue
        layer = mark_to_layer.get((project, mark))
        if layer is None:
            continue
        start = _f(row.get("settle_start"))
        end = _f(row.get("settle_end"))
        if start is None or end is None:
            continue
        grouped[project] = {
            "area": row.get("area"),
            "project_name": project,
        }
        deltas[project][layer] = period_delta(start, end)
        mark_at[project][layer] = mark

    out: list[dict[str, Any]] = []
    for project, meta in grouped.items():
        for layer_from, layer_to, d0, d1, cmm in consecutive_layer_pairs(deltas.get(project) or {}):
            out.append(
                {
                    "area": meta.get("area"),
                    "project_name": project,
                    "layer_from": layer_from,
                    "layer_to": layer_to,
                    "mark_from": mark_at[project].get(layer_from),
                    "mark_to": mark_at[project].get(layer_to),
                    "delta_from": d0,
                    "delta_to": d1,
                    "compress_mm": cmm,
                }
            )
    out.sort(
        key=lambda r: (
            str(r.get("area") or ""),
            str(r.get("project_name") or ""),
            int(r.get("layer_from") or 0),
        )
    )
    logger.info("layer_compress pairs=%s projects=%s", len(out), len(grouped))
    return out


def rank_by_area(gathered: dict[str, list[dict[str, Any]]], **_: Any) -> list[dict[str, Any]]:
    """全市平原按行政区汇总层位 0 周期沉降，供柱图（不是 t_station 全表）。"""
    rows = list(gathered.get("q_layer0") or [])
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        area = str(row.get("area") or "未知").strip() or "未知"
        delta = _f(row.get("delta_mm"))
        b = buckets.setdefault(
            area,
            {"area": area, "station_count": 0, "sum_delta_mm": 0.0, "min_delta_mm": None, "max_delta_mm": None},
        )
        b["station_count"] += 1
        if delta is None:
            continue
        b["sum_delta_mm"] += delta
        b["min_delta_mm"] = delta if b["min_delta_mm"] is None else min(float(b["min_delta_mm"]), delta)
        b["max_delta_mm"] = delta if b["max_delta_mm"] is None else max(float(b["max_delta_mm"]), delta)
    out = list(buckets.values())
    out.sort(key=lambda r: float(r.get("sum_delta_mm") or 0.0))
    return out
