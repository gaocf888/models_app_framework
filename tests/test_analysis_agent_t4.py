"""T4：声明式 tables/charts + bar/pie/line 程序渲染。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.analysis_agent.graph.builder import _route_after_quality, build_analysis_agent_graph
from app.analysis_agent.graph.orchestrator import SlotOrchestrator
from app.analysis_agent.graph.state import AnalysisAgentState
from app.analysis_agent.renderers.charts_extra import chart_from_config
from app.analysis_agent.renderers.configured_viz import prepare_chapter_viz, render_configured_table
from app.analysis_agent.report_spec import load_report_spec
from app.analysis_agent.slots.kinds import AnalysisAgentSlot
from app.analysis_agent.slots.registry import clear_slot_cache


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    clear_slot_cache()
    yield
    clear_slot_cache()


def test_overheat_report_loads_configured_viz() -> None:
    spec = load_report_spec("overheat_guidance")
    assert spec is not None
    assert any(t.get("id") == "t_ch3_region_stats" for t in spec.tables)
    assert any(c.get("chart_type") == "line" for c in spec.charts)
    assert any(c.get("attach_to_chapter") == "ch3_stats" for c in spec.charts)


def test_chart_from_config_bar_pie_line() -> None:
    rows = [
        {"area": "A", "cnt": 3},
        {"area": "B", "cnt": 5},
    ]
    bar = chart_from_config(
        chart_id="c1",
        chart_type="bar",
        title="t",
        rows=rows,
        x_field="area",
        y_field="cnt",
    )
    assert bar and bar["chart_type"] == "bar"
    pie = chart_from_config(
        chart_id="c2",
        chart_type="pie",
        title="t",
        rows=rows,
        x_field="area",
        y_field="cnt",
    )
    assert pie and pie["chart_type"] == "pie"

    line_rows = [
        {"t": "1", "v": 1.0, "layer": "L1"},
        {"t": "2", "v": 2.0, "layer": "L1"},
        {"t": "1", "v": 1.5, "layer": "L2"},
        {"t": "2", "v": 2.5, "layer": "L2"},
    ]
    line = chart_from_config(
        chart_id="c3",
        chart_type="line",
        title="line",
        rows=line_rows,
        x_field="t",
        y_field="v",
        series_field="layer",
    )
    assert line and line["chart_type"] == "line"
    assert len(line["spec"]["series"]) == 2


def test_prepare_chapter_viz_emits_configured() -> None:
    gathered = {
        "q3a": [
            {"region": "炉膛", "event_count": 12},
            {"region": "过热器", "event_count": 7},
        ]
    }
    out = prepare_chapter_viz(
        chapter_id="ch3_stats",
        report_tables=[
            {
                "id": "t1",
                "title": "区域表",
                "source_item_ids": ["q3a"],
                "attach_to_chapter": "ch3_stats",
            }
        ],
        report_charts=[
            {
                "id": "c1",
                "title": "区域柱图",
                "chart_type": "bar",
                "source_item_ids": ["q3a"],
                "x_field": "region",
                "y_field": "event_count",
                "attach_to_chapter": "ch3_stats",
            }
        ],
        gathered_data=gathered,
    )
    assert len(out["tables"]) == 1
    assert len(out["charts"]) == 1
    assert out["charts"][0]["configured"] is True
    assert "t1" in out["note"]
    assert out["table_markdowns"]


def test_render_configured_table_empty_still_payload() -> None:
    rendered = render_configured_table(
        {
            "id": "t_empty",
            "title": "空表",
            "source_item_ids": ["q_missing"],
            "attach_to_chapter": "ch_x",
        },
        gathered_data={},
    )
    assert rendered is not None
    md, tbl = rendered
    assert tbl["id"] == "t_empty"
    assert tbl.get("configured") is True


def test_slot_prepare_pushes_sse_events() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    slot = AnalysisAgentSlot(id="ch3_stats", kind="llm_section", title="三、统计")
    from app.analysis_agent.slots.serialize import slot_to_dict

    state: AnalysisAgentState = {
        "ordered_slots": [slot_to_dict(slot)],
        "slot_index": 0,
        "gathered_data": {
            "q3a": [{"region": "A", "event_count": 2}, {"region": "B", "event_count": 4}]
        },
        "report_tables": [
            {
                "id": "t_ch3",
                "title": "表",
                "source_item_ids": ["q3a"],
                "attach_to_chapter": "ch3_stats",
            }
        ],
        "report_charts": [
            {
                "id": "c_ch3",
                "title": "图",
                "chart_type": "bar",
                "source_item_ids": ["q3a"],
                "x_field": "region",
                "y_field": "event_count",
                "attach_to_chapter": "ch3_stats",
            }
        ],
        "options": {"chart_mode": "auto"},
        "pending_events": [],
    }
    out = orch.run_slot_prepare(state)
    events = out.get("pending_events") or []
    types = [e.get("event") for e in events]
    assert "analysis_agent_table_payload" in types
    assert "analysis_agent_chart_payload" in types
    assert (out.get("_prepared_viz") or {}).get("tables")


def test_graph_has_chapter_pipeline() -> None:
    orch = SlotOrchestrator(hybrid_rag=MagicMock(), nl2sql_service=MagicMock())
    graph, _cp = build_analysis_agent_graph(orch)
    if graph is None:
        pytest.skip("langgraph not available")
    nodes = set(graph.get_graph().nodes.keys()) if hasattr(graph, "get_graph") else set()
    if nodes:
        assert "chapter_pipeline" in nodes
    assert _route_after_quality({}) == "chapter_pipeline"
