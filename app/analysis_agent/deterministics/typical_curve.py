"""月报典型曲线：覆盖名单内选站 + 配对辅助序列 + 择优。禁止 LLM 选站。

定场地必须有地下水；气象、孔压只在站定了之后加分。
右轴仅地下水 / 孔隙水 / 气象。GNSS、光纤不参与。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from app.analysis_agent.deterministics.coverage import canonical_project
from app.analysis_agent.deterministics.typical_sites import (
    in_typical_axis_sites,
    in_typical_pick_sites,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

# 择优权重（配置化后置）：完整率 / |相关| / |Δ| 归一化
_W_COMPLETE = 0.4
_W_CORR = 0.3
_W_DELTA = 0.3
_SECOND_COMPLETE_MIN = 0.5

_AXIS_META = {
    "elevation": {"aux_name": "地下水标高", "legend_suffix": "地下水", "unit": "m"},
    "rain": {"aux_name": "降水", "legend_suffix": "气象", "unit": "mm"},
    "pressure": {"aux_name": "孔隙水压力", "legend_suffix": "孔隙水", "unit": ""},
}


def _f(val: Any) -> float | None:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _as_day(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    return s[:10] if len(s) >= 10 else s


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 3:
        return 0.0
    xs, ys = xs[:n], ys[:n]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    denx = sum((a - mx) ** 2 for a in xs) ** 0.5
    deny = sum((b - my) ** 2 for b in ys) ** 0.5
    if denx == 0 or deny == 0:
        return 0.0
    return abs(num / (denx * deny))


def _series_by_project(
    rows: list[dict[str, Any]],
    value_field: str,
) -> dict[str, dict[str, list[tuple[str, float]]]]:
    """project → station_key → [(day, value), ...]。无 station_name 时 station_key=project。"""
    out: dict[str, dict[str, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project_name") or "").strip()
        if not project:
            continue
        canon = canonical_project(project, "fcb") or project
        val = _f(row.get(value_field))
        day = _as_day(row.get("data_time") or row.get("x"))
        if val is None or not day:
            continue
        st = str(row.get("station_name") or "").strip() or canon
        out[canon][st].append((day, val))
    return out


def _desc_station_series(
    by_station: dict[str, list[tuple[str, float]]],
) -> tuple[str, list[tuple[str, float]]]:
    """对比方式：station_name 倒序第一口；该口无点则空（不扫下一台）。"""
    if not by_station:
        return "", []
    name = sorted(by_station.keys(), reverse=True)[0]
    return name, list(by_station.get(name) or [])


def _align(left: list[tuple[str, float]], right: list[tuple[str, float]]) -> list[dict[str, Any]]:
    rmap = {d: v for d, v in right}
    out: list[dict[str, Any]] = []
    for day, lv in left:
        if day in rmap:
            out.append({"x": day, "settle_mm": lv, "aux_value": rmap[day]})
    return out


def _score(*, complete: float, corr: float, delta_norm: float) -> float:
    return _W_COMPLETE * complete + _W_CORR * corr + _W_DELTA * delta_norm


def _annotate_axis(
    aligned: list[dict[str, Any]],
    *,
    project: str,
    kind: str,
    settle_station: str,
    aux_station: str,
    complete: float,
    corr: float,
    delta_norm: float,
) -> dict[str, Any]:
    meta = _AXIS_META[kind]
    left_legend = f"{settle_station}分层标" if settle_station else "分层标"
    right_legend = f"{aux_station}{meta['legend_suffix']}" if aux_station else meta["legend_suffix"]
    score = _score(complete=complete, corr=corr, delta_norm=delta_norm)
    for p in aligned:
        p.update(
            {
                "project_name": project,
                "aux_kind": kind,
                "aux_name": meta["aux_name"],
                "aux_unit": meta["unit"],
                "settle_station_name": settle_station,
                "aux_station_name": aux_station,
                "left_legend": left_legend,
                "right_legend": right_legend,
                "score": score,
                "complete_rate": complete,
            }
        )
    return {
        "kind": kind,
        "complete": complete,
        "corr": corr,
        "delta_norm": delta_norm,
        "score": score,
        "settle_station": settle_station,
        "aux_station": aux_station,
        "rows": aligned,
    }


def typical_curve(gathered: dict[str, list[dict[str, Any]]], **_: Any) -> list[dict[str, Any]]:
    """
    选 |delta_mm| 最大且 ∈ 分层标∩地下水公共集、水位窗内有序列的层位 0 站。
    用绝对值选站：沉/弹同等对待（符号约定仍为负沉正弹，仅择优不看符号）。
    候选双轴：水位 elevation（主路径） / 降水 / 孔压×第一压缩层。
    月报默认 Top1。
    """
    layer0 = [
        r
        for r in (gathered.get("q_layer0") or [])
        if isinstance(r, dict) and in_typical_pick_sites(str(r.get("project_name") or ""))
    ]
    ranked = sorted(layer0, key=lambda r: abs(_f(r.get("delta_mm")) or 0.0), reverse=True)
    max_abs = abs(_f(ranked[0].get("delta_mm")) or 0.0) if ranked else 0.0

    fcb_series = _series_by_project(list(gathered.get("q_typical_fcb") or []), "total_settle")
    dxswj_series = _series_by_project(list(gathered.get("q_typical_dxswj") or []), "elevation")
    qxz_series = _series_by_project(list(gathered.get("q_typical_qxz") or []), "real_time_rain")
    kxs_series = _series_by_project(list(gathered.get("q_typical_kxsylj") or []), "pressure")
    compress = [
        r
        for r in (gathered.get("q_compress") or [])
        if isinstance(r, dict) and int(r.get("layer_from") or -1) == 0 and int(r.get("layer_to") or -1) == 1
    ]
    compress_by = {str(r.get("project_name") or ""): r for r in compress}

    candidates: list[dict[str, Any]] = []
    picked_station: dict[str, Any] | None = None
    settle_pts: list[tuple[str, float]] = []
    settle_station = ""

    for row in ranked:
        project = str(row.get("project_name") or "").strip()
        if not project:
            continue
        aux_station, water = _desc_station_series(dxswj_series.get(project) or {})
        if not water:
            continue
        mark = str(row.get("station_name") or "").strip()
        by_mark = fcb_series.get(project) or {}
        settle = list(by_mark.get(mark) or [])
        if not settle:
            continue
        picked_station = row
        settle_pts = settle
        settle_station = mark
        aligned = _align(settle, water)
        complete = (len(aligned) / max(len(settle), 1)) if settle else 0.0
        corr = _pearson(
            [p["settle_mm"] for p in aligned],
            [p["aux_value"] for p in aligned],
        )
        delta_norm = (abs(_f(row.get("delta_mm")) or 0.0) / max_abs) if max_abs else 0.0
        candidates.append(
            _annotate_axis(
                aligned,
                project=project,
                kind="elevation",
                settle_station=settle_station,
                aux_station=aux_station,
                complete=complete,
                corr=corr,
                delta_norm=delta_norm,
            )
        )
        break

    if picked_station is None:
        logger.info("typical_curve no dxswj pair; chapter should write 待补充")
        return []

    project = str(picked_station.get("project_name") or "")
    delta_norm = (abs(_f(picked_station.get("delta_mm")) or 0.0) / max_abs) if max_abs else 0.0

    if in_typical_axis_sites(project, "rain"):
        rain_st, rain = _desc_station_series(qxz_series.get(project) or {})
        aligned = _align(settle_pts, rain) if rain else []
        if aligned:
            complete = len(aligned) / max(len(settle_pts), 1)
            corr = _pearson([p["settle_mm"] for p in aligned], [p["aux_value"] for p in aligned])
            candidates.append(
                _annotate_axis(
                    aligned,
                    project=project,
                    kind="rain",
                    settle_station=settle_station,
                    aux_station=rain_st,
                    complete=complete,
                    corr=corr,
                    delta_norm=delta_norm,
                )
            )

    if in_typical_axis_sites(project, "pressure") and project in compress_by:
        pres_st, pressure = _desc_station_series(kxs_series.get(project) or {})
        aligned = _align(settle_pts, pressure) if pressure else []
        if aligned:
            complete = len(aligned) / max(len(settle_pts), 1)
            corr = _pearson([p["settle_mm"] for p in aligned], [p["aux_value"] for p in aligned])
            candidates.append(
                _annotate_axis(
                    aligned,
                    project=project,
                    kind="pressure",
                    settle_station=settle_station,
                    aux_station=pres_st,
                    complete=complete,
                    corr=corr,
                    delta_norm=delta_norm,
                )
            )

    candidates.sort(key=lambda c: float(c.get("score") or 0.0), reverse=True)
    if not candidates:
        return []
    top = candidates[0]
    chosen = list(top["rows"])
    if len(candidates) > 1:
        second = candidates[1]
        if float(second.get("complete") or 0.0) >= _SECOND_COMPLETE_MIN:
            logger.info(
                "typical_curve second axis below default Top1 policy, skipped kind=%s",
                second.get("kind"),
            )
    for row in chosen:
        row["winner_kind"] = top["kind"]
        row["score"] = top["score"]
    logger.info(
        "typical_curve picked project=%s kind=%s settle=%s aux=%s points=%s score=%s",
        project,
        top.get("kind"),
        top.get("settle_station"),
        top.get("aux_station"),
        len(chosen),
        top.get("score"),
    )
    return chosen
