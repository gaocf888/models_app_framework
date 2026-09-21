"""V2-P0：自动报告 API options（query 可空、非法 area 422）。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.analysis_agent import AnalysisAgentRunRequest


def test_query_optional_for_subsidence() -> None:
    req = AnalysisAgentRunRequest(
        user_id="u1",
        session_id="s1",
        analysis_type="subsidence_daily",
    )
    assert req.query == ""
    assert req.options.start_time == ""


def test_invalid_area_rejected() -> None:
    with pytest.raises(ValidationError):
        AnalysisAgentRunRequest(
            user_id="u1",
            session_id="s1",
            analysis_type="subsidence_daily",
            options={"area": "不存在的区"},
        )


def test_paired_dates_ok() -> None:
    req = AnalysisAgentRunRequest(
        user_id="u1",
        session_id="s1",
        analysis_type="subsidence_monthly",
        options={"start_time": "2026-07-01", "end_time": "2026-08-01", "area": "朝阳区"},
    )
    assert req.options.area == "朝阳区"
