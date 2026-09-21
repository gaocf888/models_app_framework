"""V2-P0：上一完整周期与区划解析。"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.analysis_agent.period import (
    PeriodValidationError,
    canonical_query,
    default_window,
    resolve_area,
    resolve_period,
)
from app.models.analysis_agent import AnalysisAgentOptions, AnalysisAgentRunRequest


def test_weekly_window_from_wednesday() -> None:
    # 2026-09-16 周三 → 上一自然周 9/7（一）～ 9/14（一）
    start, end, label = default_window("subsidence_weekly", today=date(2026, 9, 16))
    assert start.date() == date(2026, 9, 7)
    assert end.date() == date(2026, 9, 14)
    assert "7日" in label


def test_quarterly_previous_quarter() -> None:
    start, end, label = default_window("subsidence_quarterly", today=date(2026, 9, 15))
    assert start.date() == date(2026, 4, 1)
    assert end.date() == date(2026, 7, 1)
    assert "第二季度" in label


def test_yearly_previous_year() -> None:
    start, end, label = default_window("subsidence_yearly", today=date(2026, 9, 15))
    assert start.date() == date(2025, 1, 1)
    assert end.date() == date(2026, 1, 1)
    assert label == "2025年"


def test_daily_yesterday() -> None:
    start, end, _label = default_window("subsidence_daily", today=date(2026, 9, 15))
    assert start.date() == date(2026, 9, 14)
    assert end.date() == date(2026, 9, 15)


def test_end_date_only_is_next_midnight() -> None:
    # 闭区间日期 8/1～8/1：end 日期-only 视为次日 0 点
    resolved = resolve_period(
        "subsidence_daily",
        {"start_time": "2026-08-01", "end_time": "2026-08-01"},
        today=date(2026, 9, 15),
    )
    assert resolved.t_start == "2026-08-01T00:00:00"
    assert resolved.t_end == "2026-08-02T00:00:00"


def test_start_only_rejected() -> None:
    with pytest.raises(PeriodValidationError):
        resolve_period("subsidence_daily", {"start_time": "2026-08-01"})


def test_resolve_area_city_alias() -> None:
    assert resolve_area("") is None
    assert resolve_area("全市") is None
    assert resolve_area("朝阳") == "朝阳区"


def test_canonical_query_citywide() -> None:
    resolved = resolve_period("subsidence_monthly", {}, today=date(2026, 9, 15))
    q = canonical_query("subsidence_monthly", resolved)
    assert "全市" in q
    assert "月报" in q


def test_api_start_end_must_pair() -> None:
    with pytest.raises(ValidationError):
        AnalysisAgentOptions(start_time="2026-08-01")
