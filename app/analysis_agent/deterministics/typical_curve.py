"""月报典型曲线：覆盖名单内选站 + 配对辅助序列 + 择优。禁止 LLM 选站。GNSS 一期关闭。"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from app.analysis_agent.deterministics.coverage import canonical_project, in_coverage, project_names
from app.core.logging import get_logger

logger = get_logger(__name__)

# 择优权重（配置化后置）：完整率 / |相关| / |Δ| 归一化
_W_COMPLETE = 0.4
_W_CORR = 0.3
_W_DELTA = 0.3
_SECOND_COMPLETE_MIN = 0.5


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


def _best_aux_series(by_station: dict[str, list[tuple[str, float]]]) -> list[tuple[str, float]]:
    if not by_station:
        return []
    ranked = sorted(by_station.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    return list(ranked[0][1])


def _align(left: list[tuple[str, float]], right: list[tuple[str, float]]) -> list[dict[str, Any]]:
    rmap = {d: v for d, v in right}
    out: list[dict[str, Any]] = []
    for day, lv in left:
        if day in rmap:
            out.append({"x": day, "settle_mm": lv, "aux_value": rmap[day]})
    return out


def _score(*, complete: float, corr: float, delta_norm: float) -> float:
    return _W_COMPLETE * complete + _W_CORR * corr + _W_DELTA * delta_norm


def typical_curve(gathered: dict[str, list[dict[str, Any]]], **_: Any) -> list[dict[str, Any]]:
    """
    选 |delta_mm| 最大且 ∈ dxswj 覆盖、水位窗内有序列的层位 0 站。
    候选双轴：水位 elevation（主路径） / 降水 / 孔压×第一压缩层；GNSS 名单空则关闭。
    月报默认 Top1。
    """
    layer0 = [r for r in (gathered.get("q_layer0") or []) if isinstance(r, dict)]
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

    gnss_names = project_names("gnss")
    if gnss_names:
        logger.info("typical_curve gnss coverage non-empty n=%s (direction still unused until compose)", len(gnss_names))
    else:
        logger.info("typical_curve gnss direction closed: empty coverage")

    candidates: list[dict[str, Any]] = []
    picked_station: dict[str, Any] | None = None
    settle_pts: list[tuple[str, float]] = []

    for row in ranked:
        project = str(row.get("project_name") or "").strip()
        if not project or not in_coverage(project, "dxswj"):
            continue
        water = _best_aux_series(dxswj_series.get(project) or {})
        if not water:
            continue
        mark = str(row.get("station_name") or "").strip()
        by_mark = fcb_series.get(project) or {}
        settle = list(by_mark.get(mark) or []) or _best_aux_series(by_mark)
        if not settle:
            continue
        picked_station = row
        settle_pts = settle
        aligned = _align(settle, water)
        complete = (len(aligned) / max(len(settle), 1)) if settle else 0.0
        corr = _pearson(
            [p["settle_mm"] for p in aligned],
            [p["aux_value"] for p in aligned],
        )
        delta_norm = (abs(_f(row.get("delta_mm")) or 0.0) / max_abs) if max_abs else 0.0
        for p in aligned:
            p.update(
                {
                    "project_name": project,
                    "aux_kind": "elevation",
                    "aux_name": "地下水位",
                    "aux_unit": "m",
                    "score": _score(complete=complete, corr=corr, delta_norm=delta_norm),
                    "complete_rate": complete,
                }
            )
        candidates.append(
            {
                "kind": "elevation",
                "complete": complete,
                "corr": corr,
                "delta_norm": delta_norm,
                "score": _score(complete=complete, corr=corr, delta_norm=delta_norm),
                "rows": aligned,
            }
        )
        break

    if picked_station is None:
        logger.info("typical_curve no dxswj pair; chapter should write 待补充")
        return []

    project = str(picked_station.get("project_name") or "")
    delta_norm = (abs(_f(picked_station.get("delta_mm")) or 0.0) / max_abs) if max_abs else 0.0

    if in_coverage(project, "qxz"):
        rain = _best_aux_series(qxz_series.get(project) or {})
        aligned = _align(settle_pts, rain)
        if aligned:
            complete = len(aligned) / max(len(settle_pts), 1)
            corr = _pearson([p["settle_mm"] for p in aligned], [p["aux_value"] for p in aligned])
            for p in aligned:
                p.update(
                    {
                        "project_name": project,
                        "aux_kind": "rain",
                        "aux_name": "降水量",
                        "aux_unit": "mm",
                    }
                )
            candidates.append(
                {
                    "kind": "rain",
                    "complete": complete,
                    "corr": corr,
                    "delta_norm": delta_norm,
                    "score": _score(complete=complete, corr=corr, delta_norm=delta_norm),
                    "rows": aligned,
                }
            )

    if in_coverage(project, "kxsylj") and project in compress_by:
        pressure = _best_aux_series(kxs_series.get(project) or {})
        aligned = _align(settle_pts, pressure)
        if aligned:
            complete = len(aligned) / max(len(settle_pts), 1)
            corr = _pearson([p["settle_mm"] for p in aligned], [p["aux_value"] for p in aligned])
            for p in aligned:
                p.update(
                    {
                        "project_name": project,
                        "aux_kind": "pressure",
                        "aux_name": "孔隙水压力",
                        "aux_unit": "",
                    }
                )
            candidates.append(
                {
                    "kind": "pressure",
                    "complete": complete,
                    "corr": corr,
                    "delta_norm": delta_norm,
                    "score": _score(complete=complete, corr=corr, delta_norm=delta_norm),
                    "rows": aligned,
                }
            )

    candidates.sort(key=lambda c: float(c.get("score") or 0.0), reverse=True)
    if not candidates:
        return []
    top = candidates[0]
    chosen = list(top["rows"])
    if len(candidates) > 1:
        second = candidates[1]
        if float(second.get("complete") or 0.0) >= _SECOND_COMPLETE_MIN:
            logger.info("typical_curve second axis below default Top1 policy, skipped kind=%s", second.get("kind"))
    for row in chosen:
        row["winner_kind"] = top["kind"]
        row["score"] = top["score"]
    logger.info(
        "typical_curve picked project=%s kind=%s points=%s score=%s",
        project,
        top.get("kind"),
        len(chosen),
        top.get("score"),
    )
    return chosen
