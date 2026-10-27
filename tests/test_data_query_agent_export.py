"""浏览 / 解析 CSV 导出：不连 PG。"""

from __future__ import annotations

import pytest

from app.core.config import get_app_config
from app.data_query_agent.browse import BrowseRequestError, fetch_browse_export
from app.data_query_agent.csv_export import csv_filename, table_to_csv_bytes
from app.data_query_agent.export_store import (
    ExportRequestError,
    clear_memory_exports_for_tests,
    get_parse_export_snapshot,
)
from app.data_query_agent.graph.runner import DataQueryAgentGraphRunner
from app.data_query_agent.hitl import clear_memory_sessions_for_tests
from app.data_query_agent.checkpoint import clear_memory_checkpoints_for_tests
from app.models.data_query_agent import DataQueryAgentResumeRequest, DataQueryAgentRunRequest
from app.services.data_query_agent_stream_control import DataQueryAgentStreamControl
from app.services.data_query_agent_trace_store import reset_data_query_agent_trace_store_for_tests
from tests.test_data_query_agent_browse import _FakeExecutor
from tests.test_data_query_agent_runner import _FakeNL2SQL


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


def test_table_to_csv_uses_title_and_bom() -> None:
    raw = table_to_csv_bytes(
        [{"key": "station_id", "title": "测位 ID"}, {"key": "total_settle_mm", "title": "沉降量(mm)"}],
        [
            {"station_id": "F2(望京)", "total_settle_mm": -1.5, "hud_entity_id": "F2(望京)"},
            {"station_id": "a,b", "total_settle_mm": None},
        ],
    )
    text = raw.decode("utf-8-sig")
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "测位 ID" in text
    assert "hud_entity_id" not in text.split("\n", 1)[0]
    assert '"a,b"' in text or "a,b" in text
    skipped = table_to_csv_bytes(
        [
            {"key": "station_id", "title": "测位 ID"},
            {"key": "hud_entity_id", "title": "HUD"},
        ],
        [{"station_id": "F2", "hud_entity_id": "F2"}],
    )
    header = skipped.decode("utf-8-sig").split("\n", 1)[0]
    assert "HUD" not in header
    assert "hud_entity_id" not in header


def test_csv_filename_strips_unsafe() -> None:
    name = csv_filename("browse", "fcb", "朝阳区")
    assert name.startswith("browse-fcb-")
    assert name.endswith(".csv")
    assert "/" not in name


@pytest.mark.asyncio
async def test_browse_export_rep_station_csv() -> None:
    exe = _FakeExecutor(total=2)
    payload = await fetch_browse_export(library_id="fcb", executor=exe)
    assert payload["result_grain"] == "station"
    assert payload["total"] == 2
    body = table_to_csv_bytes(payload["columns"], payload["list"])
    text = body.decode("utf-8-sig")
    assert "沉降量" in text or "测位" in text or "station" in text.lower()
    assert "F28" in text or "F2" in text


@pytest.mark.asyncio
async def test_browse_export_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_QUERY_AGENT_EXPORT_MAX_ROWS", "1")
    get_app_config.cache_clear()
    exe = _FakeExecutor(total=2)
    with pytest.raises(BrowseRequestError) as ei:
        await fetch_browse_export(library_id="fcb", executor=exe)
    assert ei.value.status_code == 413
    assert ei.value.code == "export_too_large"
    assert ei.value.extra.get("total") == 2
    assert ei.value.extra.get("max_rows") == 1
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_parse_export_replays_without_second_nl2sql() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    result = await runner.run_stream(
        DataQueryAgentRunRequest(
            user_id="user1",
            session_id="sess1",
            query="朝阳区年沉降比较大的监测点",
            library_id="fcb",
        )
    )
    token = result["export_token"]
    n_calls = len(fake.calls)
    snap = get_parse_export_snapshot(token, user_id="user1", session_id="sess1")
    assert snap["library_id"] == "fcb"
    assert snap["columns"]
    assert snap["list"]
    assert len(fake.calls) == n_calls
    csv = table_to_csv_bytes(snap["columns"], snap["list"])
    assert csv.startswith(b"\xef\xbb\xbf")


@pytest.mark.asyncio
async def test_parse_export_identity_mismatch() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    result = await runner.run_stream(
        DataQueryAgentRunRequest(
            user_id="user1",
            session_id="sess1",
            query="朝阳区最新分层标",
            library_id="fcb",
        )
    )
    with pytest.raises(ExportRequestError) as ei:
        get_parse_export_snapshot(result["export_token"], user_id="user1", session_id="other")
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_hitl_interrupt_has_no_export_token_until_resume() -> None:
    fake = _FakeNL2SQL()
    runner = DataQueryAgentGraphRunner(nl2sql=fake, stream_control=DataQueryAgentStreamControl())
    interrupt = await runner.run_stream(
        DataQueryAgentRunRequest(user_id="user1", session_id="sess1", query="查数据")
    )
    assert interrupt.get("export_token") in (None, "")
    with pytest.raises(ExportRequestError):
        get_parse_export_snapshot("dqx_missing", user_id="user1", session_id="sess1")
    resumed = await runner.resume_stream(
        DataQueryAgentResumeRequest(
            user_id="user1",
            session_id="sess1",
            resume_token=interrupt["resume_token"],
            library_id="fcb",
        )
    )
    assert str(resumed.get("export_token") or "").startswith("dqx_")
    snap = get_parse_export_snapshot(
        resumed["export_token"], user_id="user1", session_id="sess1"
    )
    assert snap["list"]
