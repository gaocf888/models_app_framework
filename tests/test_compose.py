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
    assert not any(
        c.get("id") == "c_plain_map" and c.get("attach_to_chapter") == "plain_monitoring"
        for c in spec.charts
    )


def test_daily_weekly_plain_monitoring_no_map_variant() -> None:
    for analysis_type in ("subsidence_daily", "subsidence_weekly"):
        spec = load_report_spec(analysis_type)
        assert spec is not None
        assert not any(
            str(c.get("id") or "").startswith("c_plain_map") for c in spec.charts
        )


def test_daily_plain_monitoring_asks_districts_and_extremes() -> None:
    spec = load_report_spec("subsidence_daily")
    assert spec is not None
    ch = next(c for c in spec.chapters if c.id == "plain_monitoring")
    assert "q_layer0" in ch.source_item_ids
    assert "q_area_rank" in ch.source_item_ids
    outline = " ".join(ch.outline or [])
    constraints = " ".join(ch.constraints or [])
    assert "行政区" in outline
    assert "最大" in outline and "次之" in outline
    assert "平均Δ" not in outline
    assert "禁止" in constraints
    assert "回弹" in constraints
    instruction = ch.narrative_instruction or ""
    assert "平均沉降量" in instruction


def test_weekly_monthly_key_area_uses_q_key_area() -> None:
    for analysis_type in ("subsidence_weekly", "subsidence_monthly"):
        spec = load_report_spec(analysis_type)
        assert spec is not None
        ch = next(c for c in spec.chapters if c.id == "key_area")
        assert list(ch.source_item_ids) == ["q_key_area"]
        plan_ids = {t["item_id"] for t in spec.plan_tasks}
        assert "q_key_area" in plan_ids
        assert "q_compress" in plan_ids
        assert "q_layer0" in plan_ids
        assert not any(t.get("id") == "t_key_area" for t in spec.tables)


def test_monthly_compose_basics_no_plain_no_alert() -> None:
    spec = load_report_spec("subsidence_monthly")
    assert spec is not None
    ids = [c.id for c in spec.chapters]
    assert ids[0] == "cover"
    assert ids[1] == "monthly_basics"
    assert "monthly_overview" in ids
    assert "typical_curve" in ids
    assert "station_ops" not in ids
    assert "plain_monitoring" not in ids
    assert "device_alert" not in ids
    basics = next(c for c in spec.chapters if c.id == "monthly_basics")
    assert list(basics.source_item_ids) == ["q_ops_month_offline_alarm"]
    assert "{{NARRATIVE}}" in (basics.static_body or "")
    assert "同步完成各类监测数据的采集、清洗、校核及分析工作" in (basics.static_body or "")
    assert "月内各类监测设施运行稳定，监测数据真实可靠" in (basics.static_body or "")
    assert "本月发生设备离线告警" in (basics.narrative_instruction or "")
    assert not any(t.get("attach_to_chapter") == "monthly_basics" for t in spec.tables)
    assert not any(c.get("attach_to_chapter") == "monthly_basics" for c in spec.charts)
    overview = next(c for c in spec.chapters if c.id == "monthly_overview")
    assert overview.title == "本月监测情况"
    plan_ids = {t["item_id"] for t in spec.plan_tasks}
    assert "q_ops_month_offline_alarm" in plan_ids
    month_sql = next(t for t in spec.plan_tasks if t["item_id"] == "q_ops_month_offline_alarm")
    assert month_sql["sql_file"] == "om_month_offline_alarm.sql"
    assert month_sql["executor"] == "sql_template"


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
    assert int(alert_tbl.get("max_rows") or 0) == 100


def test_quarterly_compose_districts_and_layers() -> None:
    spec = load_report_spec("subsidence_quarterly")
    assert spec is not None
    ids = [c.id for c in spec.chapters]
    assert ids[0] == "cover"
    assert ids[1] == "quarterly_preface"
    assert "station_ops" not in ids
    assert "device_alert" not in ids
    assert "regional_results" in ids
    assert "district_intro" in ids
    assert "layer_intro" in ids
    district_ids = [i for i in ids if i.startswith("district_results__")]
    layer_ids = [i for i in ids if i.startswith("layer_results__")]
    annex_ids = [i for i in ids if i.startswith("annex_layer__")]
    assert district_ids == [
        "district_results__chaoyang",
        "district_results__haidian",
        "district_results__tongzhou",
        "district_results__shunyi",
        "district_results__daxing",
        "district_results__changping",
        "district_results__pinggu",
        "district_results__yanqing",
    ]
    assert len(layer_ids) == 4
    assert "layer_results__l01" in layer_ids
    assert len(annex_ids) == 4
    plan_ids = {t["item_id"] for t in spec.plan_tasks}
    assert "q_fcb_endpoints" in plan_ids
    assert "q_compress" in plan_ids
    assert "q_key_area" in plan_ids
    assert any(t.get("attach_to_chapter") == "city_plain" for t in spec.tables)
    city_tbl = next(t for t in spec.tables if t.get("id") == "t_city_area_rank")
    assert "平均沉降量/毫米" in (city_tbl.get("columns") or [])
    assert "avg_delta_mm" in (city_tbl.get("column_keys") or [])
    city_bar = next(c for c in spec.charts if c.get("id") == "c_city_area_bar")
    assert city_bar.get("y_field") == "avg_delta_mm"
    assert any(c.get("chart_type") == "map_placeholder" for c in spec.charts)
    assert any(c.get("row_filter", {}).get("equals") == "朝阳区" for c in spec.charts)
    assert not any(
        c.get("id") == "c_district_bar__haidian" or str(c.get("id") or "").endswith("__haidian")
        for c in spec.charts
    )
    annex_titles = {c.id: c.title for c in spec.chapters if c.id.startswith("annex_layer__")}
    assert annex_titles["annex_layer__l01"].startswith("附表2")
    assert annex_titles["annex_layer__l12"].startswith("附表3")
    assert annex_titles["annex_layer__l23"].startswith("附表4")
    assert annex_titles["annex_layer__l34"].startswith("附表5")
    ground = next(c for c in spec.chapters if c.id == "annex_ground")
    assert ground.title.startswith("附表1")
    preface = next(c for c in spec.chapters if c.id == "quarterly_preface")
    assert preface.title == "一、前言"
    body = preface.static_body or ""
    assert "京地〔2026〕16号" in body
    assert "北京市地质矿产勘查院向北京市地质环境监测所下达" in body
    assert "掌握北京市地面沉降发育现状和变化趋势" in body
    assert "为全市地面沉降防控及城市规划建设提供技术支撑" in body
    assert "{{NARRATIVE}}" in body
    assert "巡查" in (preface.narrative_instruction or "")
    city = next(c for c in spec.chapters if c.id == "city_plain")
    assert "最大" in " ".join(city.outline or [])
    assert any(c.get("id") == "c_city_map" for c in spec.charts)
    key = next(c for c in spec.chapters if c.id == "key_area")
    assert key.title.startswith("四、")
    assert "压缩层" in (key.narrative_instruction or "")
    conclusion = next(c for c in spec.chapters if c.id == "conclusion")
    assert conclusion.title == "五、结论"
    cons = " ".join(conclusion.constraints or [])
    assert "禁止把各层站点数相加" in cons


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
