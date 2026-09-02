"""data_query_agent trace 独立键前缀。"""

from __future__ import annotations

from app.services.data_query_agent_service import DataQueryAgentService
from app.services.data_query_agent_trace_store import (
    reset_data_query_agent_trace_store_for_tests,
    save_data_query_agent_trace,
)


def setup_function() -> None:
    reset_data_query_agent_trace_store_for_tests()


def test_trace_list_stats_and_get() -> None:
    save_data_query_agent_trace(
        {
            "request_id": "rid-fcb-1",
            "user_id": "u1",
            "library_id": "fcb",
            "status": "success",
            "result_grain": "station",
            "hud_enabled": True,
            "warnings": ["hud_series_truncated"],
        }
    )
    save_data_query_agent_trace(
        {
            "request_id": "rid-jyb-1",
            "user_id": "u1",
            "library_id": "jyb",
            "status": "error",
            "warnings": [],
        }
    )
    svc = DataQueryAgentService()
    listed = svc.list_traces(limit=20, offset=0)
    assert listed.total == 2
    stats = svc.get_trace_stats()
    assert stats.by_library_id.get("fcb") == 1
    assert stats.by_status.get("success") == 1
    assert stats.warnings.get("hud_series_truncated") == 1
    one = svc.get_trace("rid-fcb-1")
    assert one is not None
    assert one["library_id"] == "fcb"
    filtered = svc.list_traces(limit=20, offset=0, library_id="jyb")
    assert filtered.total == 1
    assert svc.get_trace("missing") is None
