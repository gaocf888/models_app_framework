from __future__ import annotations

import pytest
from starlette.requests import Request

from app.api.request_logging import _query_summary, should_skip_request_logging


def test_should_skip_health_and_metrics() -> None:
    assert should_skip_request_logging("/metrics") is True
    assert should_skip_request_logging("/health") is True
    assert should_skip_request_logging("/api/health") is True


def test_should_skip_configured_prefix() -> None:
    assert should_skip_request_logging("/chatbot/media/abc.png", ("/chatbot/media",)) is True
    assert should_skip_request_logging("/chatbot/chat", ("/chatbot/media",)) is False


def test_query_summary_truncates_long_values() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/chatbot/sessions",
        "headers": [],
        "query_string": b"user_id=u1&q=" + b"x" * 100,
    }
    request = Request(scope)
    summary = _query_summary(request)
    assert "user_id=u1" in summary
    assert summary.endswith("...") or "q=" in summary


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/rag/query", False),
        ("/nl2sql/query", False),
    ],
)
def test_business_paths_are_logged(path: str, expected: bool) -> None:
    assert should_skip_request_logging(path) is expected
