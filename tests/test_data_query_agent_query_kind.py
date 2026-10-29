"""解析 SQL 入口闸：像查数才继续。"""

from __future__ import annotations

import pytest

from app.core.config import get_app_config
from app.data_query_agent.checkpoint import clear_memory_checkpoints_for_tests
from app.data_query_agent.export_store import clear_memory_exports_for_tests
from app.data_query_agent.graph.runner import DataQueryAgentGraphRunner
from app.data_query_agent.hitl import clear_memory_sessions_for_tests
from app.data_query_agent.query_kind import classify_data_query_kind
from app.models.data_query_agent import DataQueryAgentRunRequest
from app.services.data_query_agent_stream_control import DataQueryAgentStreamControl
from app.services.data_query_agent_trace_store import reset_data_query_agent_trace_store_for_tests
from tests.test_data_query_agent_runner import _FakeNL2SQL


def test_chitchat_who_are_you() -> None:
    r = classify_data_query_kind("你是谁")
    assert not r.looks_like_data_query
    assert r.kind == "chitchat"


def test_hello_is_chitchat() -> None:
    r = classify_data_query_kind("你好")
    assert not r.looks_like_data_query
    assert r.kind == "chitchat"


def test_knowledge_what_is_fcb() -> None:
    r = classify_data_query_kind("什么是分层标")
    assert not r.looks_like_data_query
    assert r.kind == "knowledge"


def test_knowledge_principle() -> None:
    r = classify_data_query_kind("分层标原理")
    assert not r.looks_like_data_query
    assert r.kind == "knowledge"


def test_real_query_still_data() -> None:
    r = classify_data_query_kind("朝阳区年沉降比较大的监测点")
    assert r.looks_like_data_query


def test_lookup_data_unresolved_library() -> None:
    r = classify_data_query_kind("查数据")
    assert r.looks_like_data_query


def test_hello_plus_real_query_is_data() -> None:
    r = classify_data_query_kind("你好，帮我查询朝阳区沉降")
    assert r.looks_like_data_query


def test_library_name_only_is_data() -> None:
    r = classify_data_query_kind("分层标")
    assert r.looks_like_data_query


@pytest.fixture(autouse=True)
def _mem_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_QUERY_AGENT_CHECKPOINT_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_SESSION_STORE_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_TRACE_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_LIBRARY_LLM_ENABLED", "false")
    get_app_config.cache_clear()
    clear_memory_sessions_for_tests()
    clear_memory_checkpoints_for_tests()
    clear_memory_exports_for_tests()
    reset_data_query_agent_trace_store_for_tests()
    yield
    clear_memory_sessions_for_tests()
    clear_memory_checkpoints_for_tests()
    clear_memory_exports_for_tests()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_who_are_you_with_library_id_does_not_nl2sql() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    result = await runner.run_stream(
        DataQueryAgentRunRequest(
            user_id="user1",
            session_id="sess1",
            query="你是谁",
            library_id="fcb",
        )
    )
    assert result["status"] == "error"
    assert result["event"] == "data_query_error"
    assert result["error"] == "not_data_query"
    assert "智能客服" in (result.get("message") or "")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_knowledge_with_library_id_does_not_nl2sql() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    result = await runner.run_stream(
        DataQueryAgentRunRequest(
            user_id="user1",
            session_id="sess1",
            query="什么是分层标",
            library_id="fcb",
        )
    )
    assert result["error"] == "not_data_query"
    assert fake.calls == []
