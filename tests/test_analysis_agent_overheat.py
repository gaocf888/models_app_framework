from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.analysis_agent.graph.runner import AnalysisAgentGraphRunner
from app.analysis_agent.plans.loader import load_plan_tasks
from app.analysis_agent.renderers.charts_extra import chart_from_table
from app.analysis_agent.slots.registry import get_agent_slots, registry_available
from app.models.nl2sql import NL2SQLQueryResponse


@pytest.mark.parametrize("analysis_type", ["overheat_guidance"])
def test_registry_available(analysis_type: str) -> None:
    assert registry_available(analysis_type)
    slots = get_agent_slots(analysis_type)
    assert len(slots) == 9
    kinds = {s.kind for s in slots}
    assert "llm_section" in kinds
    assert "template_deterministic" not in kinds


def test_plan_template_loads() -> None:
    tasks = load_plan_tasks("overheat_guidance", version="v1")
    ids = {t["item_id"] for t in tasks}
    assert "q1" in ids and "q6d" in ids
    assert len(tasks) >= 15


def test_chart_from_table_classification() -> None:
    table = {
        "columns": ["区域", "次数"],
        "rows": [{"区域": "高温过热器", "次数": 5}, {"区域": "再热器", "次数": 2}],
    }
    ch = chart_from_table(
        table_id="t1",
        table_kind="classification",
        table=table,
        title="区域分布",
    )
    assert ch is not None
    assert ch["chart_type"] == "bar"


def test_chart_from_table_proportion() -> None:
    table = {
        "columns": ["等级", "占比"],
        "rows": [{"等级": "严重", "占比": 0.6}, {"等级": "轻微", "占比": 0.4}],
    }
    ch = chart_from_table(table_id="t2", table_kind="proportion", table=table, title="占比")
    assert ch is not None
    assert ch["chart_type"] == "pie"


@pytest.mark.asyncio
async def test_iter_stream_events_mock_nl2sql() -> None:
    hybrid = MagicMock()
    hybrid.retrieve = MagicMock(return_value=[])
    nl2sql = MagicMock()
    mock_resp = NL2SQLQueryResponse(sql="SELECT 1", rows=[{"col": 1}])
    nl2sql.query = AsyncMock(return_value=mock_resp)
    runner = AnalysisAgentGraphRunner(hybrid_rag=hybrid, nl2sql_service=nl2sql)

    async def mock_stream(*_a, **_k):
        yield "测试叙述片段。"

    runner._orch._llm.stream_chat = mock_stream  # type: ignore[method-assign]

    events: list[dict] = []
    async for ev in runner.iter_stream_events(
        user_id="u_test",
        session_id="s_test",
        analysis_type="overheat_guidance",
        query="分析一号锅炉近期超温",
        options={"enable_rag": False},
    ):
        events.append(ev)
        if ev.get("event") == "analysis_agent_finished":
            break

    names = [e.get("event") for e in events]
    assert "analysis_agent_meta" in names
    assert "analysis_agent_slot_start" in names
    assert "analysis_agent_finished" in names
    finished = [e for e in events if e.get("event") == "analysis_agent_finished"][0]
    assert finished.get("result", {}).get("request_id", "").startswith("aa_")
