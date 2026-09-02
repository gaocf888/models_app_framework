"""年沉降 / 年度变化：默认近 365 天，窗内终值 − 初值。"""

from __future__ import annotations

from datetime import datetime

from app.data_query_agent.annual import compute_annual_delta, resolve_annual_window, window_delta


def test_annual_window_defaults_to_rolling_365_not_yesterday() -> None:
    now = datetime(2026, 8, 31, 12, 0, 0)
    win = resolve_annual_window("朝阳区年沉降比较大的监测点", now=now)
    assert win["source"] == "rolling_365"
    assert win["start"] == "2025-08-31 12:00:00"
    assert win["end"] == "2026-08-31 12:00:00"


def test_annual_window_uses_intent_when_parsed() -> None:
    now = datetime(2026, 8, 31, 12, 0, 0)
    win = resolve_annual_window("近一年朝阳区分层标", now=now)
    assert win["source"] == "intent"
    assert win["start"]
    assert win["end"]


def test_window_delta_last_minus_first() -> None:
    points = [
        {"t": "2025-09-01 00:00:00", "v": -10.0},
        {"t": "2026-03-01 00:00:00", "v": -40.0},
        {"t": "2026-08-01 00:00:00", "v": -94.2},
    ]
    delta = window_delta(
        points,
        start="2025-08-31 00:00:00",
        end="2026-08-31 23:59:59",
    )
    assert delta == -84.2


def test_compute_annual_fallback_when_window_has_one_point() -> None:
    points = [
        {"t": "2025-09-01 00:00:00", "v": 0.0},
        {"t": "2026-08-01 00:00:00", "v": -12.0},
    ]
    delta = compute_annual_delta(
        points,
        start="2026-07-01 00:00:00",
        end="2026-08-31 00:00:00",
    )
    assert delta == -12.0
