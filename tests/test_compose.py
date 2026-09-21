"""V2-P1：板块库 compose 展开。"""

from __future__ import annotations

from app.analysis_agent.report_spec import load_report_spec
from app.analysis_agent.slots.registry import clear_slot_cache


def setup_function() -> None:
    clear_slot_cache()


def test_daily_compose_four_chapters() -> None:
    spec = load_report_spec("subsidence_daily")
    assert spec is not None
    ids = [c.id for c in spec.chapters]
    assert ids == ["cover", "station_ops", "plain_monitoring", "device_alert"]
    plan_ids = {t["item_id"] for t in spec.plan_tasks}
    assert "q_fcb_endpoints" in plan_ids
    assert "q_layer0" in plan_ids


def test_quarterly_compose_districts_and_layers() -> None:
    spec = load_report_spec("subsidence_quarterly")
    assert spec is not None
    ids = [c.id for c in spec.chapters]
    district_ids = [i for i in ids if i.startswith("district_results__")]
    layer_ids = [i for i in ids if i.startswith("layer_results__")]
    annex_ids = [i for i in ids if i.startswith("annex_layer__")]
    assert len(district_ids) == 8
    assert "district_results__chaoyang" in district_ids
    assert len(layer_ids) == 4
    assert "layer_results__l01" in layer_ids
    assert len(annex_ids) == 4
    plan_ids = {t["item_id"] for t in spec.plan_tasks}
    assert "q_fcb_endpoints" in plan_ids
    assert "q_compress" in plan_ids
    assert "q_key_area" in plan_ids
    assert any(t.get("attach_to_chapter") == "city_plain" for t in spec.tables)
    assert any(c.get("chart_type") == "map_placeholder" for c in spec.charts)
    assert any(c.get("row_filter", {}).get("equals") == "朝阳区" for c in spec.charts)
    annex_titles = {c.id: c.title for c in spec.chapters if c.id.startswith("annex_layer__")}
    assert annex_titles["annex_layer__l01"].startswith("附表2")
    assert annex_titles["annex_layer__l12"].startswith("附表3")
    assert annex_titles["annex_layer__l23"].startswith("附表4")
    assert annex_titles["annex_layer__l34"].startswith("附表5")
    ground = next(c for c in spec.chapters if c.id == "annex_ground")
    assert ground.title.startswith("附表1")


def test_yearly_compose_matches_quarterly_structure() -> None:
    q = load_report_spec("subsidence_quarterly")
    y = load_report_spec("subsidence_yearly")
    assert q is not None and y is not None
    assert [c.id for c in q.chapters] == [c.id for c in y.chapters]


def test_boiler_load_has_no_compose() -> None:
    spec = load_report_spec("overheat_guidance")
    assert spec is not None
    assert len(spec.chapters) == 9
    assert all(c.id != "cover" or "超温" in (spec.title or "") for c in spec.chapters[:1])
