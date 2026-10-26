"""运维 snapshot 绑定：跟沉降回退后的窗末日走。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.analysis_agent.deterministics.fcb_endpoints import load_sql_template, resolved_sql_params
from app.analysis_agent.ops_snapshot import (
    REASON_OPS_NO_DAY_BATCH,
    REASON_OPS_PROBE_ERROR,
    apply_ops_snapshot_bind,
    ops_snap_day_bounds,
)
from app.analysis_agent.period import apply_resolved_period, period_sse_dict
from app.analysis_agent.period_probe import fallback_degrade_reasons


class _FakeOpsProbe:
    def __init__(self, day_snap: datetime | None, latest: datetime | None = None) -> None:
        self.day_snap = day_snap
        self.latest = latest
        self.calls: list[tuple[datetime, datetime, str]] = []

    async def pick(self, day_start: datetime, day_end: datetime, resolved: dict) -> tuple[datetime | None, bool]:
        self.calls.append((day_start, day_end, str(resolved.get("t_end") or "")))
        if self.day_snap is not None:
            return self.day_snap, False
        return self.latest, True


class _BoomProbe:
    async def pick(self, day_start: datetime, day_end: datetime, resolved: dict) -> tuple[datetime | None, bool]:
        raise RuntimeError("db down")


def test_snap_day_midnight_end_uses_previous_calendar_day() -> None:
    start, end = ops_snap_day_bounds("2026-10-26T00:00:00")
    assert start == datetime(2026, 10, 25)
    assert end == datetime(2026, 10, 26)


def test_snap_day_quarter_end() -> None:
    start, end = ops_snap_day_bounds("2026-07-01T00:00:00")
    assert start == datetime(2026, 6, 30)
    assert end == datetime(2026, 7, 1)


def test_snap_day_non_midnight_uses_that_calendar_day() -> None:
    start, end = ops_snap_day_bounds("2026-10-25T18:20:00")
    assert start == datetime(2026, 10, 25)
    assert end == datetime(2026, 10, 26)


@pytest.mark.asyncio
async def test_bind_uses_resolved_window_after_settlement_fallback() -> None:
    opts = apply_resolved_period(
        "subsidence_quarterly",
        {
            "start_time": "2026-07-01",
            "end_time": "2026-10-01",
            "_resolved": {
                "t_start": "2026-04-01T00:00:00",
                "t_end": "2026-07-01T00:00:00",
                "area": None,
                "period_label": "2026年第二季度",
                "report_date": "2026年10月15日",
                "issue_no": "XX",
                "report_kind": "季度报告",
                "fallback": True,
            },
        },
        today=datetime(2026, 10, 15).date(),
    )
    probe = _FakeOpsProbe(day_snap=datetime(2026, 6, 30, 8, 20, 0))
    out = await apply_ops_snapshot_bind("subsidence_quarterly", opts, probe=probe)
    resolved = out["_resolved"]
    assert probe.calls[0][0] == datetime(2026, 6, 30)
    assert probe.calls[0][2] == "2026-07-01T00:00:00"
    assert resolved["ops_snap_day"] == "2026-06-30"
    assert resolved["ops_snapshot_time"] == "2026-06-30T08:20:00"
    assert resolved["ops_fallback"] is False
    assert "ops_fallback_reason" not in resolved


@pytest.mark.asyncio
async def test_bind_falls_back_to_latest_when_day_empty() -> None:
    opts = {
        "_resolved": {
            "t_start": "2026-10-19T00:00:00",
            "t_end": "2026-10-26T00:00:00",
            "area": None,
        }
    }
    probe = _FakeOpsProbe(day_snap=None, latest=datetime(2026, 10, 24, 8, 20, 0))
    out = await apply_ops_snapshot_bind("subsidence_weekly", opts, probe=probe)
    resolved = out["_resolved"]
    assert resolved["ops_snap_day"] == "2026-10-25"
    assert resolved["ops_snapshot_time"] == "2026-10-24T08:20:00"
    assert resolved["ops_fallback"] is True
    assert resolved["ops_fallback_reason"] == REASON_OPS_NO_DAY_BATCH
    assert "ops_snapshot_fallback" in fallback_degrade_reasons(resolved)


@pytest.mark.asyncio
async def test_bind_probe_error_marks_fallback() -> None:
    opts = {"_resolved": {"t_end": "2026-10-26T00:00:00"}}
    out = await apply_ops_snapshot_bind("subsidence_daily", opts, probe=_BoomProbe())
    resolved = out["_resolved"]
    assert resolved["ops_fallback"] is True
    assert resolved["ops_fallback_reason"] == REASON_OPS_PROBE_ERROR
    assert "ops_snapshot_time" not in resolved


@pytest.mark.asyncio
async def test_bind_keeps_existing_snapshot() -> None:
    opts = {
        "_resolved": {
            "t_end": "2026-10-26T00:00:00",
            "ops_snapshot_time": "2026-10-25T08:20:00",
        }
    }
    probe = _FakeOpsProbe(day_snap=datetime(2026, 1, 1, 0, 0, 0))
    out = await apply_ops_snapshot_bind("subsidence_daily", opts, probe=probe)
    assert out["_resolved"]["ops_snapshot_time"] == "2026-10-25T08:20:00"
    assert probe.calls == []


def test_period_sse_includes_ops_fields() -> None:
    sse = period_sse_dict(
        {
            "t_start": "2026-10-19T00:00:00",
            "t_end": "2026-10-26T00:00:00",
            "period_label": "2026年10月19日—10月25日",
            "area": None,
            "ops_snapshot_time": "2026-10-25T08:20:00",
            "ops_snap_day": "2026-10-25",
            "ops_fallback": False,
        }
    )
    assert sse["ops_snapshot_time"] == "2026-10-25T08:20:00"
    assert sse["ops_snap_day"] == "2026-10-25"
    assert sse["ops_fallback"] is False


def test_om_sql_params_require_snapshot() -> None:
    with pytest.raises(ValueError, match="sql_bind_missing_snapshot_time"):
        resolved_sql_params(
            {"t_start": "2026-10-19T00:00:00", "t_end": "2026-10-26T00:00:00"},
            sql_file="om_alert_list.sql",
        )


def test_om_sql_params_bind_snapshot() -> None:
    params = resolved_sql_params(
        {
            "t_start": "2026-10-19T00:00:00",
            "t_end": "2026-10-26T00:00:00",
            "ops_snapshot_time": "2026-10-25T08:20:00",
        },
        sql_file="om_ops_rates_by_type.sql",
    )
    assert params["snapshot_time"] == datetime(2026, 10, 25, 8, 20, 0)
    assert params["snapshot_time"].tzinfo is None
    assert isinstance(params["t_start"], str)


def test_om_pick_sql_binds_day_bounds() -> None:
    sql = load_sql_template("om_snapshot_pick.sql")
    assert ":snap_day_start" in sql and ":snap_day_end" in sql
    params = resolved_sql_params(
        {"t_start": "2026-10-19T00:00:00", "t_end": "2026-10-26T00:00:00"},
        extra={"snap_day_start": datetime(2026, 10, 25), "snap_day_end": datetime(2026, 10, 26)},
        sql_file="om_snapshot_pick.sql",
    )
    assert params["snap_day_start"] == datetime(2026, 10, 25)
    assert params["snap_day_end"] == datetime(2026, 10, 26)
    assert "snapshot_time" not in params or not isinstance(params.get("snapshot_time"), datetime)
