"""V2-P0：上一完整周期与区划解析。"""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from app.analysis_agent.period import (
    PeriodValidationError,
    apply_resolved_period,
    canonical_query,
    complete_window_containing,
    complete_window_ending_on_or_before,
    default_window,
    label_for_complete_window,
    previous_complete_before,
    resolve_area,
    resolve_period,
    step_back_complete_window,
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


def test_complete_window_containing_grains() -> None:
    inst = datetime(2026, 6, 15, 8, 0, 0)
    d0, d1 = complete_window_containing("subsidence_daily", inst)
    assert d0 == datetime(2026, 6, 15) and d1 == datetime(2026, 6, 16)
    w0, w1 = complete_window_containing("subsidence_weekly", inst)
    assert w0 == datetime(2026, 6, 15) and w1 == datetime(2026, 6, 22)  # 周一
    m0, m1 = complete_window_containing("subsidence_monthly", inst)
    assert m0 == datetime(2026, 6, 1) and m1 == datetime(2026, 7, 1)
    q0, q1 = complete_window_containing("subsidence_quarterly", inst)
    assert q0 == datetime(2026, 4, 1) and q1 == datetime(2026, 7, 1)
    y0, y1 = complete_window_containing("subsidence_yearly", inst)
    assert y0 == datetime(2026, 1, 1) and y1 == datetime(2027, 1, 1)


def test_complete_window_ending_on_or_before_no_overlap() -> None:
    # 请求窗从 7/15 起：含 7/10 的自然月会叠进请求窗，应再退到 6 月
    start, end = complete_window_ending_on_or_before(
        "subsidence_monthly",
        before=datetime(2026, 7, 15),
        instant=datetime(2026, 7, 10),
    )
    assert start == datetime(2026, 6, 1)
    assert end == datetime(2026, 7, 1)


def test_previous_complete_before_daily() -> None:
    start, end = previous_complete_before("subsidence_daily", datetime(2026, 10, 14))
    assert start == datetime(2026, 10, 13)
    assert end == datetime(2026, 10, 14)


def test_step_back_quarter() -> None:
    start, end = step_back_complete_window(
        "subsidence_quarterly", datetime(2026, 4, 1), datetime(2026, 7, 1)
    )
    assert start == datetime(2026, 1, 1)
    assert end == datetime(2026, 4, 1)
    assert "第一季度" in label_for_complete_window("subsidence_quarterly", start, end)


def test_apply_resolved_period_keeps_fallback_extra() -> None:
    opts = apply_resolved_period(
        "subsidence_daily",
        {
            "start_time": "2026-10-11",
            "end_time": "2026-10-11",
            "_resolved": {
                "t_start": "2026-10-10T00:00:00",
                "t_end": "2026-10-11T00:00:00",
                "area": None,
                "period_label": "2026年10月10日",
                "report_date": "2026年10月15日",
                "issue_no": "XX",
                "report_kind": "日报",
                "fallback": True,
                "fallback_reason": "no_paired_data_in_requested_window",
                "requested_t_start": "2026-10-11T00:00:00",
            },
        },
        today=date(2026, 10, 15),
    )
    resolved = opts["_resolved"]
    assert resolved["t_start"] == "2026-10-10T00:00:00"
    assert resolved["fallback"] is True
    assert resolved["requested_t_start"] == "2026-10-11T00:00:00"


def test_apply_resolved_period_keeps_ops_snapshot_extra() -> None:
    opts = apply_resolved_period(
        "subsidence_daily",
        {
            "start_time": "2026-10-25",
            "end_time": "2026-10-25",
            "_resolved": {
                "t_start": "2026-10-25T00:00:00",
                "t_end": "2026-10-26T00:00:00",
                "area": None,
                "period_label": "2026年10月25日",
                "report_date": "2026年10月26日",
                "issue_no": "XX",
                "report_kind": "日报",
                "ops_snapshot_time": "2026-10-25T08:20:00",
                "ops_snap_day": "2026-10-25",
                "ops_fallback": False,
            },
        },
        today=date(2026, 10, 26),
    )
    resolved = opts["_resolved"]
    assert resolved["ops_snapshot_time"] == "2026-10-25T08:20:00"
    assert resolved["ops_fallback"] is False
