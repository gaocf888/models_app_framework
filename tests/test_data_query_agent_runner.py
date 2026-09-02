"""数据查询智能体 runner：mock NL2SQL，不连 PG。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import get_app_config
from app.data_query_agent.checkpoint import clear_memory_checkpoints_for_tests
from app.data_query_agent.graph.runner import DataQueryAgentGraphRunner
from app.data_query_agent.hitl import clear_memory_sessions_for_tests
from app.models.data_query_agent import (
    DataQueryAgentOptions,
    DataQueryAgentResumeRequest,
    DataQueryAgentRunRequest,
)
from app.models.nl2sql import NL2SQLQueryResponse
from app.services.data_query_agent_stream_control import DataQueryAgentStreamControl
from app.services.data_query_agent_trace_store import reset_data_query_agent_trace_store_for_tests

_SERIES_COL = {
    "t_data_wash_fcb": "total_settle",
    "t_data_wash_jyb": "total_settle",
    "t_data_wash_gnss": "displacement_3d",
    "t_data_wash_dxswj": "deep",
    "t_data_wash_kxsylj": "pressure",
    "t_data_wash_qxz": "temp",
    "t_data_wash_gq": "total_settle",
}


class _FakeNL2SQL:
    def __init__(self) -> None:
        self.calls: list = []

    async def query(self, req, record_conversation: bool = True, include_parsed_intent=None):
        self.calls.append(req)
        table = (req.forced_tables or ["t_data_wash_fcb"])[0]
        col = _SERIES_COL.get(table, "total_settle")
        if table == "t_data_wash_gnss":
            assert "total_settle" not in (req.question or "")
        orig = (req.original_query or req.time_intent_text or req.question or "")
        district_q = "各区" in orig
        city_q = ("全市" in orig or "全北京" in orig) and ("平均" in orig or "汇总" in orig)
        if req.plan_item_id == "q_hud_series":
            if city_q:
                return NL2SQLQueryResponse(
                    sql=f"SELECT data_time, {col} FROM {table}",
                    rows=[{"data_time": "2026-01-01", col: -8.0}],
                )
            if district_q:
                return NL2SQLQueryResponse(
                    sql=f"SELECT area, data_time, {col} FROM {table}",
                    rows=[{"area": "朝阳区", "data_time": "2026-01-01", col: -12.0}],
                )
            return NL2SQLQueryResponse(
                sql=f"SELECT station_id, data_time, {col} FROM {table}",
                rows=[
                    {"station_id": "JZGZ", "data_time": "2020-01-01", col: -10.2},
                    {"station_id": "JZGZ", "data_time": "2021-01-01", col: -90.0},
                ],
            )
        if city_q:
            return NL2SQLQueryResponse(
                sql=f"SELECT AVG({col}) FROM {table} JOIN t_station",
                rows=[{col: -8.0, "station_count": 20}],
            )
        if district_q:
            return NL2SQLQueryResponse(
                sql=f"SELECT area, AVG({col}) FROM {table} JOIN t_station",
                rows=[{"area": "朝阳区", col: -12.0, "station_count": 3}],
            )
        row: dict = {
            "station_id": "JZGZ",
            "station_name": "金盏公交",
            "area": "朝阳区",
            col: -418.5,
            "data_time": "2026-08-01",
        }
        if table == "t_data_wash_gnss":
            row["displacement_3d"] = 12.3
            row.pop("total_settle", None)
        return NL2SQLQueryResponse(
            sql=f"SELECT station_id, {col} FROM {table} JOIN t_station",
            rows=[row],
        )


class _AlwaysCancel(DataQueryAgentStreamControl):
    async def is_cancelled(self, user_id: str, session_id: str, stream_id: str) -> bool:
        return True


@pytest.fixture(autouse=True)
def _reset_hitl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_QUERY_AGENT_CHECKPOINT_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_SESSION_STORE_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_TRACE_BACKEND", "memory")
    monkeypatch.setenv("DATA_QUERY_AGENT_LIBRARY_LLM_ENABLED", "false")
    get_app_config.cache_clear()
    clear_memory_sessions_for_tests()
    clear_memory_checkpoints_for_tests()
    reset_data_query_agent_trace_store_for_tests()
    yield
    clear_memory_sessions_for_tests()
    clear_memory_checkpoints_for_tests()
    get_app_config.cache_clear()


async def _collect(aiter):
    events = []
    async for ev in aiter:
        events.append(ev)
    return events


@pytest.mark.asyncio
async def test_run_stream_success_path1() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="朝阳区年沉降比较大的监测点",
        library_id="fcb",
    )
    events = await _collect(runner.run_stream(req))
    names = [e.get("event") for e in events]
    assert names[0] == "started"
    assert "data_query_library_hit" in names
    assert "data_query_scope_parsed" in names
    assert "data_query_result" in names
    assert names[-1] == "finished"
    hit = next(e for e in events if e["event"] == "data_query_library_hit")
    assert hit["library_id"] == "fcb"
    assert hit["source"] == "request"
    assert hit["table"] == "t_data_wash_fcb"
    scope_ev = next(e for e in events if e["event"] == "data_query_scope_parsed")
    assert (scope_ev.get("scope") or {}).get("district") == "朝阳区"
    result = next(e for e in events if e["event"] == "data_query_result")
    assert result["hud_enabled"] is True
    assert all(c.forced_tables == ["t_data_wash_fcb"] for c in fake.calls)
    assert all(c.analysis_type == "data_query" for c in fake.calls)
    assert all(c.disable_qa_slot_replay is True for c in fake.calls)
    assert all(c.on_link_failure == "refuse" for c in fake.calls)


@pytest.mark.asyncio
async def test_path2_parsed_daxing() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="大兴区有哪些分层监测点？",
    )
    events = await _collect(runner.run_stream(req))
    hit = next(e for e in events if e["event"] == "data_query_library_hit")
    assert hit["source"] == "parsed"
    assert hit["library_id"] == "fcb"
    scope_ev = next(e for e in events if e["event"] == "data_query_scope_parsed")
    assert (scope_ev.get("scope") or {}).get("district") == "大兴区"
    assert all(c.forced_tables == ["t_data_wash_fcb"] for c in fake.calls)


@pytest.mark.asyncio
async def test_include_hud_false_only_q_list() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="朝阳区最新分层标",
        library_id="fcb",
        options=DataQueryAgentOptions(include_hud=False),
    )
    events = await _collect(runner.run_stream(req))
    result = next(e for e in events if e["event"] == "data_query_result")
    assert result["hud_enabled"] is False
    assert "hud_by_station" not in result
    assert "hud_by_entity" not in result
    assert [c.plan_item_id for c in fake.calls] == ["q_list"]


@pytest.mark.asyncio
async def test_district_grain_entity_hud() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="各区平均沉降",
        library_id="fcb",
    )
    events = await _collect(runner.run_stream(req))
    result = next(e for e in events if e["event"] == "data_query_result")
    assert result["result_grain"] == "district"
    assert result["hud_enabled"] is True
    assert result["list"][0]["hud_entity_type"] == "district"
    assert "朝阳区" in result["hud_by_entity"]
    assert result["hud_by_entity"]["朝阳区"]["series"]["agg"] == "avg"
    assert {c.plan_item_id for c in fake.calls} == {"q_list", "q_hud_series"}
    progresses = [e for e in events if e["event"] == "data_query_nl2sql_progress"]
    assert progresses[0]["q_list"] == "running"
    assert progresses[-1]["q_list"] == "done"
    finished = next(e for e in events if e["event"] == "finished")
    assert finished.get("trace_id")


@pytest.mark.asyncio
async def test_explicit_district_overrides_query() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="朝阳区最新分层标",
        library_id="fcb",
        district="大兴区",
    )
    events = await _collect(runner.run_stream(req))
    scope_ev = next(e for e in events if e["event"] == "data_query_scope_parsed")
    assert (scope_ev.get("scope") or {}).get("district") == "大兴区"
    result = next(e for e in events if e["event"] == "data_query_result")
    assert "scope_nl_overridden" in (result.get("warnings") or [])


@pytest.mark.asyncio
async def test_city_grain_entity_hud() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="全市平均沉降",
        library_id="fcb",
    )
    events = await _collect(runner.run_stream(req))
    result = next(e for e in events if e["event"] == "data_query_result")
    assert result["result_grain"] == "city"
    assert result["hud_enabled"] is True
    assert result["list"][0]["hud_entity_id"] == "beijing"
    assert result["hud_by_entity"]["beijing"]["series"]["agg"] == "avg"
    assert {c.plan_item_id for c in fake.calls} == {"q_list", "q_hud_series"}


@pytest.mark.asyncio
async def test_jyb_forced_tables_not_fcb() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="朝阳区沉降大的点",
        library_id="jyb",
    )
    events = await _collect(runner.run_stream(req))
    assert any(e.get("event") == "data_query_result" for e in events)
    assert fake.calls
    assert all(c.forced_tables == ["t_data_wash_jyb"] for c in fake.calls)


@pytest.mark.asyncio
async def test_gnss_not_total_settle() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="朝阳区位移比较大的点",
        library_id="gnss",
    )
    events = await _collect(runner.run_stream(req))
    assert any(e.get("event") == "data_query_result" for e in events)
    assert all(c.forced_tables == ["t_data_wash_gnss"] for c in fake.calls)
    assert all("t_data_wash_gnss" in (c.question or "") for c in fake.calls)
    assert all("total_settle" not in (c.question or "") for c in fake.calls)


@pytest.mark.asyncio
async def test_hitl_then_resume() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(user_id="user1", session_id="sess1", query="查数据")
    events = await _collect(runner.run_stream(req))
    names = [e.get("event") for e in events]
    assert "data_query_library_input_required" in names
    assert "data_query_result" not in names
    interrupt = next(e for e in events if e["event"] == "data_query_library_input_required")
    token = interrupt["resume_token"]
    assert token.startswith("dq_")
    resume = DataQueryAgentResumeRequest(
        user_id="user1",
        session_id="sess1",
        resume_token=token,
        library_id="fcb",
    )
    resumed = await _collect(runner.resume_stream(resume))
    rnames = [e.get("event") for e in resumed]
    assert "data_query_library_hit" in rnames
    hit = next(e for e in resumed if e["event"] == "data_query_library_hit")
    assert hit["source"] == "hitl"
    assert "data_query_result" in rnames
    assert rnames[-1] == "finished"


@pytest.mark.asyncio
async def test_resume_jyb_forced_tables() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    events = await _collect(
        runner.run_stream(DataQueryAgentRunRequest(user_id="user1", session_id="sess1", query="查数据"))
    )
    token = next(e["resume_token"] for e in events if e["event"] == "data_query_library_input_required")
    resumed = await _collect(
        runner.resume_stream(
            DataQueryAgentResumeRequest(
                user_id="user1",
                session_id="sess1",
                resume_token=token,
                library_id="jyb",
            )
        )
    )
    hit = next(e for e in resumed if e["event"] == "data_query_library_hit")
    assert hit["library_id"] == "jyb"
    assert hit["source"] == "hitl"
    assert fake.calls
    assert all(c.forced_tables == ["t_data_wash_jyb"] for c in fake.calls)


@pytest.mark.asyncio
async def test_resume_from_checkpoint_after_session_cleared() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    events = await _collect(
        runner.run_stream(DataQueryAgentRunRequest(user_id="user1", session_id="sess1", query="查数据"))
    )
    token = next(e["resume_token"] for e in events if e["event"] == "data_query_library_input_required")
    clear_memory_sessions_for_tests()
    resumed = await _collect(
        runner.resume_stream(
            DataQueryAgentResumeRequest(
                user_id="user1",
                session_id="sess1",
                resume_token=token,
                library_id="jyb",
            )
        )
    )
    assert any(e.get("event") == "data_query_result" for e in resumed)
    assert all(c.forced_tables == ["t_data_wash_jyb"] for c in fake.calls)


@pytest.mark.asyncio
async def test_resume_abort_cancelled() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    req = DataQueryAgentRunRequest(user_id="user1", session_id="sess1", query="查数据")
    events = await _collect(runner.run_stream(req))
    token = next(e["resume_token"] for e in events if e["event"] == "data_query_library_input_required")
    resumed = await _collect(
        runner.resume_stream(
            DataQueryAgentResumeRequest(
                user_id="user1",
                session_id="sess1",
                resume_token=token,
                abort=True,
            )
        )
    )
    names = [e.get("event") for e in resumed]
    assert "data_query_cancelled" in names
    assert names[-1] == "finished"
    assert not fake.calls


@pytest.mark.asyncio
async def test_stop_before_acquire_no_result() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=_AlwaysCancel())
    req = DataQueryAgentRunRequest(
        user_id="user1",
        session_id="sess1",
        query="朝阳区沉降",
        library_id="fcb",
    )
    events = await _collect(runner.run_stream(req))
    names = [e.get("event") for e in events]
    assert names[0] == "started"
    assert "data_query_cancelled" in names
    assert "data_query_result" not in names
    assert not fake.calls


def test_empty_query_validation_error() -> None:
    with pytest.raises(ValidationError):
        DataQueryAgentRunRequest(user_id="user1", session_id="sess1", query="")
    with pytest.raises(ValidationError):
        DataQueryAgentRunRequest(user_id="user1", session_id="sess1", query="   ")


def test_require_disabled_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from app.api.data_query_agent import require_data_query_agent_enabled

    monkeypatch.setenv("DATA_QUERY_AGENT_ENABLED", "false")
    get_app_config.cache_clear()
    with pytest.raises(HTTPException) as ei:
        require_data_query_agent_enabled()
    assert ei.value.status_code == 503
    monkeypatch.setenv("DATA_QUERY_AGENT_ENABLED", "true")
    get_app_config.cache_clear()
