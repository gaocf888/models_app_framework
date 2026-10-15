"""无配对数据时回退到最近有数据的完整周期。"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.analysis_agent.period import FALLBACK_MAX_STEPS, apply_resolved_period
from app.analysis_agent.period_probe import (
    REASON_EXHAUSTED,
    REASON_NO_DATA,
    apply_period_data_fallback,
    fallback_degrade_reasons,
)


class _FakeProbe:
    def __init__(self, paired: set[str], max_ts: datetime | None = None) -> None:
        self.paired = paired
        self.max_ts = max_ts

    async def has_paired(self, resolved: dict) -> bool:
        return str(resolved.get("t_start") or "") in self.paired

    async def max_data_time_before(self, before: datetime, resolved: dict) -> datetime | None:
        if self.max_ts is not None and self.max_ts < before:
            return self.max_ts
        return None


@pytest.mark.asyncio
async def test_keep_requested_when_paired() -> None:
    opts = apply_resolved_period(
        "subsidence_daily",
        {"start_time": "2026-10-11", "end_time": "2026-10-11"},
        today=datetime(2026, 10, 15).date(),
    )
    probe = _FakeProbe(paired={"2026-10-11T00:00:00"})
    out = await apply_period_data_fallback("subsidence_daily", opts, probe=probe)
    resolved = out["_resolved"]
    assert resolved["t_start"] == "2026-10-11T00:00:00"
    assert not resolved.get("fallback")
    assert fallback_degrade_reasons(resolved) == []


@pytest.mark.asyncio
async def test_fallback_snaps_to_day_with_pair() -> None:
    opts = apply_resolved_period(
        "subsidence_daily",
        {"start_time": "2026-10-14", "end_time": "2026-10-14"},
        today=datetime(2026, 10, 15).date(),
    )
    probe = _FakeProbe(
        paired={"2026-10-10T00:00:00"},
        max_ts=datetime(2026, 10, 10, 8, 0, 0),
    )
    out = await apply_period_data_fallback("subsidence_daily", opts, probe=probe)
    resolved = out["_resolved"]
    assert resolved["t_start"] == "2026-10-10T00:00:00"
    assert resolved["t_end"] == "2026-10-11T00:00:00"
    assert resolved["fallback"] is True
    assert resolved["fallback_reason"] == REASON_NO_DATA
    assert resolved["requested_t_start"] == "2026-10-14T00:00:00"
    assert "period_fallback" in fallback_degrade_reasons(resolved)
    assert "10月10日" in str(resolved["period_label"])


@pytest.mark.asyncio
async def test_fallback_quarter_previous() -> None:
    opts = apply_resolved_period(
        "subsidence_quarterly",
        {"start_time": "2026-07-01", "end_time": "2026-10-01"},
        today=datetime(2026, 10, 15).date(),
    )
    # 显式 Q3 无配对；6 月有点 → 对齐到 Q2
    probe = _FakeProbe(
        paired={"2026-04-01T00:00:00"},
        max_ts=datetime(2026, 6, 15, 12, 0, 0),
    )
    out = await apply_period_data_fallback("subsidence_quarterly", opts, probe=probe)
    resolved = out["_resolved"]
    assert resolved["t_start"] == "2026-04-01T00:00:00"
    assert resolved["t_end"] == "2026-07-01T00:00:00"
    assert "第二季度" in str(resolved["period_label"])
    assert resolved["fallback"] is True


@pytest.mark.asyncio
async def test_fallback_exhausted_keeps_last_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(FALLBACK_MAX_STEPS, "subsidence_daily", 3)
    opts = apply_resolved_period(
        "subsidence_daily",
        {"start_time": "2026-10-14", "end_time": "2026-10-14"},
        today=datetime(2026, 10, 15).date(),
    )
    probe = _FakeProbe(paired=set(), max_ts=None)
    out = await apply_period_data_fallback("subsidence_daily", opts, probe=probe)
    resolved = out["_resolved"]
    assert resolved["fallback"] is True
    assert resolved["fallback_reason"] == REASON_EXHAUSTED
    assert resolved["t_end"] <= "2026-10-14T00:00:00"
    assert "period_fallback_exhausted" in fallback_degrade_reasons(resolved)
