"""T5：地降 subsidence_* 类型与季报报告规格。"""

from __future__ import annotations

import pytest

from app.analysis_agent.context_loader import load_analysis_run_context
from app.analysis_agent.plans.loader import get_synthesis_template, load_plan_tasks
from app.analysis_agent.report_spec import load_report_spec
from app.analysis_agent.slots.registry import SUPPORTED_ANALYSIS_TYPES, registry_available
from app.analysis_agent.slots.specs import is_subsidence_type, narrative_scene_for_type
from app.models.analysis_agent import AnalysisAgentRunRequest


_SUBSIDENCE = (
    "subsidence_daily",
    "subsidence_weekly",
    "subsidence_monthly",
    "subsidence_quarterly",
    "subsidence_yearly",
)


@pytest.mark.parametrize("analysis_type", _SUBSIDENCE)
def test_subsidence_types_registered(analysis_type: str) -> None:
    assert analysis_type in SUPPORTED_ANALYSIS_TYPES
    assert registry_available(analysis_type)
    assert is_subsidence_type(analysis_type)
    assert narrative_scene_for_type(analysis_type) == "analysis_agent_synthesis_subsidence"


def test_quarterly_report_structure() -> None:
    spec = load_report_spec("subsidence_quarterly")
    assert spec is not None
    assert "季度" in (spec.title or "")
    chapter_ids = {c.id for c in spec.chapters}
    assert "ch_preface" in chapter_ids
    assert "ch_city_overview" in chapter_ids
    assert "ch_layer" in chapter_ids
    assert "ch_appendix" in chapter_ids
    plan_ids = {t["item_id"] for t in spec.plan_tasks}
    assert {"q1", "q2", "q3", "q4", "q5", "q6"}.issubset(plan_ids)
    assert any(t.get("attach_to_chapter") == "ch_city_overview" for t in spec.tables)
    assert any(c.get("chart_type") == "bar" for c in spec.charts)
    assert any(c.get("chart_type") == "line" for c in spec.charts)


@pytest.mark.parametrize("analysis_type", _SUBSIDENCE)
def test_subsidence_context_loads(analysis_type: str) -> None:
    ctx = load_analysis_run_context(analysis_type)
    assert ctx.from_report_spec is True
    assert len(ctx.slots) >= 2
    assert len(ctx.plan_tasks) >= 2
    assert ctx.report_tables is not None
    assert ctx.report_charts is not None


@pytest.mark.parametrize("analysis_type", _SUBSIDENCE)
def test_subsidence_plan_and_synthesis(analysis_type: str) -> None:
    tasks = load_plan_tasks(analysis_type)
    assert any(t.get("item_id") == "q1" for t in tasks)
    tpl, scene = get_synthesis_template(analysis_type)
    assert tpl is not None
    assert "沉降" in (tpl.content or "")
    assert scene == "analysis_agent_synthesis_subsidence"


def test_api_model_accepts_subsidence_quarterly() -> None:
    req = AnalysisAgentRunRequest(
        user_id="u1",
        session_id="s1",
        analysis_type="subsidence_quarterly",
        query="请生成2024年第三季度地面沉降季报",
    )
    assert req.analysis_type == "subsidence_quarterly"


def test_placeholder_reports_minimal() -> None:
    for t in ("subsidence_daily", "subsidence_weekly", "subsidence_monthly", "subsidence_yearly"):
        spec = load_report_spec(t)
        assert spec is not None
        assert len(spec.chapters) >= 2
        assert len(spec.plan_tasks) >= 2
