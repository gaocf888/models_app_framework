"""叙述数字白名单与打字机切分。"""

from __future__ import annotations

import logging
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.analysis_agent.agents.section_agent import synthesize_section
from app.analysis_agent.deterministics.number_guard import (
    collect_allowed_numbers,
    extra_numbers,
    narrative_numbers_allowed,
)
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.renderers.typewriter import typewriter_chunks
from app.analysis_agent.slots.kinds import AnalysisAgentSlot


def test_number_guard_accepts_same_value_different_decimals() -> None:
    allowed = collect_allowed_numbers("delta_mm=12.5", "在线率100.00%", "92台")
    assert narrative_numbers_allowed("东坝沉降12.50mm，在线率100%，共计92台。", allowed)
    assert extra_numbers("编造200台", allowed) == {Decimal("200")}


def test_typewriter_chunks_split() -> None:
    assert typewriter_chunks("封面", chunk_chars=1) == ["封", "面"]
    assert typewriter_chunks("abcd", chunk_chars=2) == ["ab", "cd"]


@pytest.mark.asyncio
async def test_station_ops_rejects_hallucinated_number_keeps_streamed_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _gen(**_kwargs):
        yield "GNSS共计设备"
        yield "200台，在线率100%。"

    client = MagicMock()
    client.stream_chat = _gen
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    gathered = {
        "q_ops_rates": [
            {
                "device_type": "GNSS",
                "total": 92,
                "online_rate": 100.0,
                "complete_rate": 66.83,
                "effective_rate": 66.83,
                "offline": 0,
            }
        ]
    }
    slot = AnalysisAgentSlot(
        id="station_ops",
        kind="llm_section",
        title="监测站点运行情况",
        use_emit_tools=False,
        source_item_ids=["q_ops_rates"],
    )
    prompts = MagicMock()
    prompts.get = MagicMock(return_value=None)
    with (
        caplog.at_level(logging.INFO, logger="app.analysis_agent.agents.section_agent"),
        patch(
            "app.analysis_agent.agents.section_agent.get_synthesis_template",
            return_value=(None, None),
        ),
    ):
        result = await synthesize_section(
            prompts=prompts,
            slot=slot,
            query="日报",
            gathered_data=gathered,
            context_snippets=[],
            task_status={},
            hybrid_rag=None,
            analysis_type="subsidence_daily",
            llm_client=client,
            use_react_agent=False,
            on_delta=on_delta,
        )
    joined = "".join(deltas)
    assert result.markdown == "GNSS共计设备200台，在线率100%。"
    assert joined == result.markdown
    assert deltas == ["GNSS共计设备", "200台，在线率100%。"]
    messages = [r.getMessage() for r in caplog.records]
    assert any("section numbers rejected slot=station_ops extra=['200']" in m for m in messages)
    assert any(
        "section numbers result slot=station_ops numbers_ok=false source=llm" in m
        for m in messages
    )


@pytest.mark.asyncio
async def test_station_ops_keeps_llm_when_numbers_in_allowlist(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def _gen(**_kwargs):
        yield "GNSS共计设备92台，在线率100%。"

    client = MagicMock()
    client.stream_chat = _gen
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    gathered = {
        "q_ops_rates": [
            {
                "device_type": "GNSS",
                "total": 92,
                "online_rate": 100.0,
                "complete_rate": 66.83,
                "effective_rate": 66.83,
                "offline": 0,
            }
        ]
    }
    slot = AnalysisAgentSlot(
        id="station_ops",
        kind="llm_section",
        title="运行",
        use_emit_tools=False,
        source_item_ids=["q_ops_rates"],
    )
    prompts = MagicMock()
    prompts.get = MagicMock(return_value=None)
    with (
        caplog.at_level(logging.INFO, logger="app.analysis_agent.agents.section_agent"),
        patch(
            "app.analysis_agent.agents.section_agent.get_synthesis_template",
            return_value=(None, None),
        ),
    ):
        result = await synthesize_section(
            prompts=prompts,
            slot=slot,
            query="日报",
            gathered_data=gathered,
            context_snippets=[],
            task_status={},
            hybrid_rag=None,
            analysis_type="subsidence_daily",
            llm_client=client,
            use_react_agent=False,
            on_delta=on_delta,
        )
    assert result.markdown.startswith("GNSS共计设备92台")
    assert "".join(deltas) == result.markdown
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "section numbers result slot=station_ops numbers_ok=true source=llm" in m
        for m in messages
    )


@pytest.mark.asyncio
async def test_cover_static_markdown_typewrites_live() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    slot = AnalysisAgentSlot(
        id="cover",
        kind="static_markdown",
        title="",
        static_body="内部材料\n\n日报\n",
    )
    state: AnalysisAgentState = {
        "request_id": "aa_cover",
        "query": "q",
        "analysis_type": "subsidence_daily",
        "options": {"narrative_streaming": True},
        "gathered_data": {},
        "task_status": {},
        "intent_context": [],
        "pending_events": [],
        "slot_index": 0,
        "summary_parts": [],
        "structured_report": {"sections": [], "tables": [], "charts": []},
        "slot_trace": [],
    }
    out, chunks, live = await orch._synthesize_slot(state, slot)
    assert live is True
    assert chunks == []
    assert "内部材料" in (out.markdown or "")
    texts = [
        str(e.get("text") or "")
        for e in (state.get("pending_events") or [])
        if e.get("event") == "analysis_agent_summary_delta"
    ]
    assert "".join(texts) == (out.markdown or "")
    assert len(texts) > 1
