"""年沉降 / 年度变化：窗内终值 − 初值（方案 §7.3）。

未解析到明确时间窗时用近 365 天，不用基座默认的「昨天」统计口径。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.nl2sql.time_intent_display import (
    extract_time_window_from_question,
    resolve_statistical_time_range_display,
)


def resolve_annual_window(query: str, *, now: datetime | None = None) -> dict[str, str]:
    """解析年变化时间窗；问句无时间则近 365 天。"""
    ref = now or datetime.now()
    win = extract_time_window_from_question(query)
    if win is None:
        end = ref
        start = ref - timedelta(days=365)
        return {
            "start": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end": end.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "rolling_365",
            "tag": "",
        }
    start_s, end_s = resolve_statistical_time_range_display(query, now=ref)
    return {
        "start": start_s,
        "end": end_s,
        "source": "intent",
        "tag": str(win[2] or ""),
    }


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("T", " ").replace("Z", "")
    if "+" in text[10:]:
        text = text.split("+")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def window_delta(
    points: list[dict[str, Any]],
    *,
    start: str,
    end: str,
) -> float | None:
    """points: {t, v}，按时间排序后取窗内末日 − 窗内首日。"""
    start_dt = _parse_ts(start)
    end_dt = _parse_ts(end)
    if start_dt is None or end_dt is None:
        return None
    in_win: list[tuple[datetime, float]] = []
    for p in points:
        ts = _parse_ts(p.get("t"))
        val = _to_float(p.get("v"))
        if ts is None or val is None:
            continue
        if start_dt <= ts <= end_dt:
            in_win.append((ts, val))
    if len(in_win) < 2:
        return None
    in_win.sort(key=lambda x: x[0])
    return in_win[-1][1] - in_win[0][1]


def compute_annual_delta(
    points: list[dict[str, Any]],
    *,
    start: str,
    end: str,
) -> float | None:
    """优先用意图窗；窗内不足两点则退化为该站最新观测向前 365 天。"""
    direct = window_delta(points, start=start, end=end)
    if direct is not None:
        return direct
    parsed: list[tuple[datetime, float]] = []
    for p in points:
        ts = _parse_ts(p.get("t"))
        val = _to_float(p.get("v"))
        if ts is not None and val is not None:
            parsed.append((ts, val))
    if len(parsed) < 2:
        return None
    parsed.sort(key=lambda x: x[0])
    latest = parsed[-1][0]
    start_fb = latest - timedelta(days=365)
    in_fb = [x for x in parsed if start_fb <= x[0] <= latest]
    if len(in_fb) < 2:
        return None
    return in_fb[-1][1] - in_fb[0][1]
