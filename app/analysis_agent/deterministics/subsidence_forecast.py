"""季报沉降趋势预测：典型代表站 + 月均 OLS 外推下一季度。

选站口径对齐 typical_curve（分层标∩地下水，按 |Δ| 择优）。
水位/降水/孔压仅作依据与叙述相关，不参与联合回归。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.analysis_agent.deterministics.typical_curve import (
    _desc_station_series,
    _f,
    _pearson,
    _series_by_project,
)
from app.analysis_agent.deterministics.typical_sites import (
    in_typical_axis_sites,
    in_typical_pick_sites,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_MIN_MONTHS = 6
_HORIZON_MONTHS = 3
_SERIES_OBS = "实测累计沉降"
_SERIES_FIT = "拟合趋势"
_SERIES_PRED = "预测沉降"


def _month_key(day: str) -> str:
    return day[:7] if len(day) >= 7 else ""


def _next_month(ym: str) -> str:
    """YYYY-MM → 下一自然月。"""
    try:
        y, m = int(ym[:4]), int(ym[5:7])
    except (TypeError, ValueError):
        return ""
    if m >= 12:
        return f"{y + 1:04d}-01"
    return f"{y:04d}-{m + 1:02d}"


def _monthly_mean(pts: list[tuple[str, float]]) -> dict[str, float]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for day, val in pts:
        mk = _month_key(day)
        if mk:
            buckets[mk].append(val)
    return {m: sum(vs) / len(vs) for m, vs in buckets.items() if vs}


def _monthly_sum(pts: list[tuple[str, float]]) -> dict[str, float]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for day, val in pts:
        mk = _month_key(day)
        if mk:
            buckets[mk].append(val)
    return {m: sum(vs) for m, vs in buckets.items() if vs}


def _ols(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """返回 (截距 a, 斜率 b, R²)。"""
    n = min(len(xs), len(ys))
    if n < 2:
        return 0.0, 0.0, 0.0
    xs, ys = xs[:n], ys[:n]
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    b = (num / den) if den else 0.0
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for y in ys)
    ss_res = sum((y - (a + b * x)) ** 2 for x, y in zip(xs, ys))
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    return a, b, max(0.0, min(1.0, r2))


def _align_months(
    settle: dict[str, float],
    other: dict[str, float],
) -> tuple[list[float], list[float]]:
    months = sorted(set(settle) & set(other))
    return [settle[m] for m in months], [other[m] for m in months]


def _insufficient(reason: str) -> list[dict[str, Any]]:
    logger.info("subsidence_forecast insufficient: %s", reason)
    return [
        {
            "row_kind": "meta",
            "status": "insufficient",
            "reason": reason,
            "disclaimer": "基于近12个月监测序列的统计外推，非物理模型；样本不足时不得编造预测。",
            "lookback_months": 12,
            "horizon_months": _HORIZON_MONTHS,
        }
    ]


def _direction_label(delta: float) -> str:
    if delta < -1e-9:
        return "沉降"
    if delta > 1e-9:
        return "回弹"
    return "基本持平"


def subsidence_forecast(
    gathered: dict[str, list[dict[str, Any]]],
    **_: Any,
) -> list[dict[str, Any]]:
    """
    产出：
    - row_kind=chart：折线图长表（x/y/series）
    - row_kind=meta：叙述事实（斜率、R²、相关、下季预测Δ等）
    """
    layer0 = [
        r
        for r in (gathered.get("q_layer0") or [])
        if isinstance(r, dict) and in_typical_pick_sites(str(r.get("project_name") or ""))
    ]
    ranked = sorted(layer0, key=lambda r: abs(_f(r.get("delta_mm")) or 0.0), reverse=True)

    fcb_series = _series_by_project(list(gathered.get("q_forecast_fcb") or []), "total_settle")
    dxswj_series = _series_by_project(list(gathered.get("q_forecast_dxswj") or []), "elevation")
    qxz_series = _series_by_project(
        list(gathered.get("q_forecast_qxz") or []),
        "real_time_rain",
        device_type="qxz",
        prefer_station_code=True,
    )
    kxs_series = _series_by_project(list(gathered.get("q_forecast_kxsylj") or []), "pressure")

    picked: dict[str, Any] | None = None
    settle_pts: list[tuple[str, float]] = []
    settle_station = ""
    aux_station = ""
    water_pts: list[tuple[str, float]] = []

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
        picked = row
        settle_pts = settle
        settle_station = mark
        water_pts = water
        break

    if picked is None:
        return _insufficient("无分层标与地下水配对序列")

    project = str(picked.get("project_name") or "").strip()
    settle_m = _monthly_mean(settle_pts)
    water_m = _monthly_mean(water_pts)
    months = sorted(settle_m.keys())
    if len(months) < _MIN_MONTHS:
        return _insufficient(f"月均沉降点不足{_MIN_MONTHS}个月（现有{len(months)}个月）")

    ys = [settle_m[m] for m in months]
    xs = [float(i) for i in range(len(months))]
    a, b, r2 = _ols(xs, ys)
    fitted = [a + b * t for t in xs]

    last_obs = ys[-1]
    last_month = months[-1]
    pred_months: list[str] = []
    pred_vals: list[float] = []
    cur = last_month
    for i in range(_HORIZON_MONTHS):
        cur = _next_month(cur)
        if not cur:
            break
        pred_months.append(cur)
        pred_vals.append(a + b * float(len(months) + i))

    if len(pred_vals) < _HORIZON_MONTHS:
        return _insufficient("无法生成完整下一季度预测月点")

    pred_end = pred_vals[-1]
    pred_delta = pred_end - last_obs

    water_xs, water_ys = _align_months(settle_m, water_m)
    corr_water = _pearson(water_xs, water_ys) if len(water_xs) >= 3 else 0.0

    rain_station = ""
    corr_rain = 0.0
    rain_note = "本期无降水配对"
    if in_typical_axis_sites(project, "rain"):
        rain_station, rain_pts = _desc_station_series(qxz_series.get(project) or {})
        if rain_pts:
            rain_m = _monthly_sum(rain_pts)
            rx, ry = _align_months(settle_m, rain_m)
            if len(rx) >= 3:
                corr_rain = _pearson(rx, ry)
                rain_note = f"与月降水相关绝对值约{corr_rain:.2f}"
            else:
                rain_note = "降水月点不足，相关未计算"
        else:
            rain_note = "场地有气象覆盖但窗内无降水序列"

    pressure_station = ""
    corr_pressure = 0.0
    pressure_note = "本期无孔隙水配对"
    if in_typical_axis_sites(project, "pressure"):
        pressure_station, pressure_pts = _desc_station_series(kxs_series.get(project) or {})
        if pressure_pts:
            pressure_m = _monthly_mean(pressure_pts)
            px, py = _align_months(settle_m, pressure_m)
            if len(px) >= 3:
                corr_pressure = _pearson(px, py)
                pressure_note = f"与月均孔隙水压力相关绝对值约{corr_pressure:.2f}"
            else:
                pressure_note = "孔隙水月点不足，相关未计算"
        else:
            pressure_note = "场地有孔压覆盖但窗内无孔隙水序列"

    complete = len(months) / 12.0
    out: list[dict[str, Any]] = []

    for m, y, fit in zip(months, ys, fitted):
        out.append({"row_kind": "chart", "x": m, "y": round(y, 3), "series": _SERIES_OBS})
        out.append({"row_kind": "chart", "x": m, "y": round(fit, 3), "series": _SERIES_FIT})

    # 衔接：预测系列从末观测月起画，保证折线连续
    out.append(
        {
            "row_kind": "chart",
            "x": last_month,
            "y": round(last_obs, 3),
            "series": _SERIES_PRED,
        }
    )
    for m, pv in zip(pred_months, pred_vals):
        out.append(
            {
                "row_kind": "chart",
                "x": m,
                "y": round(pv, 3),
                "series": _SERIES_PRED,
            }
        )

    out.append(
        {
            "row_kind": "meta",
            "status": "ok",
            "project_name": project,
            "settle_station_name": settle_station,
            "water_station_name": aux_station,
            "rain_station_name": rain_station,
            "pressure_station_name": pressure_station,
            "lookback_months": 12,
            "history_month_count": len(months),
            "history_start_month": months[0],
            "history_end_month": last_month,
            "horizon_months": _HORIZON_MONTHS,
            "forecast_months": "、".join(pred_months),
            "slope_mm_per_month": round(b, 4),
            "r2": round(r2, 4),
            "last_obs_mm": round(last_obs, 3),
            "pred_end_mm": round(pred_end, 3),
            "pred_delta_next_q_mm": round(pred_delta, 3),
            "pred_direction": _direction_label(pred_delta),
            "corr_water": round(corr_water, 4),
            "corr_rain": round(corr_rain, 4),
            "corr_pressure": round(corr_pressure, 4),
            "rain_note": rain_note,
            "pressure_note": pressure_note,
            "complete_rate": round(complete, 4),
            "period_delta_mm": _f(picked.get("delta_mm")),
            "disclaimer": "基于近12个月监测序列的统计外推，非物理模型；须结合地下水、孔隙水与降水依据解读，并注明不确定性。",
        }
    )

    logger.info(
        "subsidence_forecast project=%s settle=%s water=%s months=%s slope=%s r2=%s pred_delta=%s",
        project,
        settle_station,
        aux_station,
        len(months),
        round(b, 4),
        round(r2, 4),
        round(pred_delta, 3),
    )
    return out
