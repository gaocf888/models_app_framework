from app.core.metrics_path import collapse_dynamic_path


def test_collapse_uuid_and_numeric():
    assert (
        collapse_dynamic_path("/rag/jobs/550e8400-e29b-41d4-a716-446655440000")
        == "/rag/jobs/{id}"
    )
    assert collapse_dynamic_path("/rag/jobs/12345") == "/rag/jobs/{id}"


def test_collapse_preserves_static():
    assert collapse_dynamic_path("/chatbot/chat") == "/chatbot/chat"
    assert collapse_dynamic_path("/health") == "/health"
