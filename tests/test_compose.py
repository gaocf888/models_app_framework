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
    assert "q_area_rank" in plan_ids
    assert any(
        c.get("id") == "c_plain_map" and c.get("attach_to_chapter") == "plain_monitoring"
        for c in spec.charts
    )


def test_daily_plain_monitoring_asks_districts_and_extremes() -> None:
    spec = load_report_spec("subsidence_daily")
    assert spec is not None
    ch = next(c for c in spec.chapters if c.id == "plain_monitoring")
    assert "q_layer0" in ch.source_item_ids
    assert "q_area_rank" in ch.source_item_ids
    outline = " ".join(ch.outline or [])
    assert "行政区" in outline
    assert "极值" in outline
    assert "极值" in (ch.narrative_instruction or "")


def test_monthly_compose_includes_ops_and_alert() -> None:
    spec = load_report_spec("subsidence_monthly")
    assert spec is not None
    ids = [c.id for c in spec.chapters]
    assert ids[0] == "cover"
    assert "station_ops" in ids
    assert "device_alert" in ids
    assert "monthly_overview" in ids
    assert "typical_curve" in ids
    plan_ids = {t["item_id"] for t in spec.plan_tasks}
    assert "q_ops_rates" in plan_ids
    assert "q_ops_offline_list" in plan_ids
    assert "q_alert_list" in plan_ids
    assert "q_alert_count_by_type" in plan_ids


def test_om_plan_items_forced_sql_template() -> None:
    from app.analysis_agent.compose import enforce_om_sql_template_plan_items

    fixed = enforce_om_sql_template_plan_items(
        [
            {
                "item_id": "q_ops_rates",
                "executor": "nl2sql",
                "sql_file": "om_ops_rates_by_type.sql",
            },
            {
                "item_id": "q_alert_list",
                "executor": "placeholder",
                "sql_file": "om_alert_list.sql",
            },
            {"item_id": "q_layer0", "executor": "python", "python_fn": "slice_layer0"},
        ]
    )
    by_id = {x["item_id"]: x for x in fixed}
    assert by_id["q_ops_rates"]["executor"] == "sql_template"
    assert by_id["q_alert_list"]["executor"] == "sql_template"
    assert by_id["q_layer0"]["executor"] == "python"

    import pytest

    with pytest.raises(ValueError, match="om_plan_item_requires_om_sql_file"):
        enforce_om_sql_template_plan_items(
            [{"item_id": "q_ops_rates", "executor": "nl2sql", "sql_file": "fcb_period_endpoints.sql"}]
        )


def test_station_ops_section_has_offline_and_multi_rate_chart() -> None:
    spec = load_report_spec("subsidence_daily")
    assert spec is not None
    ops = next(c for c in spec.chapters if c.id == "station_ops")
    assert "q_ops_offline_list" in ops.source_item_ids
    assert "{{NARRATIVE}}" in (ops.static_body or "")
    assert any(t.get("id") == "t_station_ops_offline" for t in spec.tables)
    chart = next(c for c in spec.charts if c.get("id") == "c_station_ops_rates")
    assert isinstance(chart.get("y_fields"), list)
    assert len(chart["y_fields"]) == 3
    alert = next(c for c in spec.chapters if c.id == "device_alert")
    assert "q_alert_count_by_type" in alert.source_item_ids
    alert_tbl = next(t for t in spec.tables if t.get("id") == "t_device_alert")
    assert "seq" in (alert_tbl.get("column_keys") or [])


def test_quarterly_compose_districts_and_layers() -> None:
    spec = load_report_spec("subsidence_quarterly")
    assert spec is not None
    ids = [c.id for c in spec.chapters]
    assert "station_ops" in ids
    assert "device_alert" in ids
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
    assert "q_ops_rates" in plan_ids
    assert "q_ops_offline_list" in plan_ids
    assert "q_alert_summary" in plan_ids
    assert "q_alert_count_by_type" in plan_ids
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
