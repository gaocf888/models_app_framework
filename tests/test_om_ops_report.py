"""运维报告：离线/告警 SQL 存在性、类型展示名、多指标柱图、static 叙述包裹。"""

from __future__ import annotations

from pathlib import Path

from app.analysis_agent.deterministics.fcb_endpoints import load_sql_template
from app.analysis_agent.deterministics.om_type_display import (
    apply_om_type_display,
    load_om_report_display_map,
    map_om_type_display,
)
from app.analysis_agent.renderers.charts_extra import chart_from_config
from app.analysis_agent.renderers.configured_viz import render_configured_chart, render_configured_table
from app.analysis_agent.renderers.static_narrative import (
    NARRATIVE_MARKER,
    apply_static_narrative,
    split_static_narrative,
)


def test_om_sql_templates_exist_and_have_latest_batch() -> None:
    for name in (
        "om_ops_rates_by_type.sql",
        "om_ops_offline_list.sql",
        "om_alert_summary.sql",
        "om_alert_count_by_type.sql",
        "om_alert_list.sql",
    ):
        sql = load_sql_template(name)
        assert "om_result." in sql
        assert "snapshot_time" in sql
        assert "max(snapshot_time)" in sql.replace(" ", "").lower() or "max(snapshot_time)" in sql


def test_om_offline_sql_filters_status_and_days() -> None:
    sql = load_sql_template("om_ops_offline_list.sql")
    assert "status = 0" in sql
    assert "offline_days" in sql
    assert "t_om_offline_device_list" in sql


def test_om_alert_list_has_seq_and_type_order() -> None:
    sql = load_sql_template("om_alert_list.sql")
    assert "ROW_NUMBER()" in sql
    assert "ORDER BY a.type, a.device_name" in sql


def test_om_report_display_map() -> None:
    load_om_report_display_map.cache_clear()
    mapping = load_om_report_display_map()
    assert mapping.get("基岩标/分层标") == "静力水准仪"
    assert mapping.get("地下水位井") == "地下水位计"
    assert mapping.get("孔隙水压力井") == "孔隙水压力计"
    assert mapping.get("光纤") == "光纤自动解调仪"
    assert map_om_type_display("GNSS") == "GNSS"
    rows = apply_om_type_display(
        [{"device_type": "基岩标/分层标", "online_rate": 100}, {"type": "光纤", "x": 1}]
    )
    assert rows[0]["device_type"] == "静力水准仪"
    assert rows[1]["type"] == "光纤自动解调仪"


def test_static_narrative_marker() -> None:
    pre, post = split_static_narrative(f"开篇\n\n{NARRATIVE_MARKER}\n\n①脚注")
    assert pre == "开篇"
    assert post == "①脚注"
    assert apply_static_narrative(f"开篇\n\n{NARRATIVE_MARKER}\n\n①脚注", "类型段") == (
        "开篇\n\n类型段\n\n①脚注"
    )
    assert apply_static_narrative("仅前缀", "中段") == "仅前缀\n\n中段"


def test_grouped_bar_y_fields() -> None:
    rows = [
        {
            "device_type": "GNSS",
            "online_rate": 95.0,
            "complete_rate": 82.0,
            "effective_rate": 80.0,
        },
        {
            "device_type": "静力水准仪",
            "online_rate": 100.0,
            "complete_rate": 96.0,
            "effective_rate": 78.0,
        },
    ]
    ch = chart_from_config(
        chart_id="c1",
        chart_type="bar",
        title="运行图",
        rows=rows,
        x_field="device_type",
        y_fields=[
            {"field": "online_rate", "name": "在线率(%)"},
            {"field": "complete_rate", "name": "完整率(%)"},
            {"field": "effective_rate", "name": "有效率(%)"},
        ],
    )
    assert ch is not None
    assert ch["spec"].get("grouped") is True
    assert len(ch["spec"]["series"]) == 3

    rendered = render_configured_chart(
        {
            "id": "c_station_ops_rates",
            "title": "监测设备运行情况统计图",
            "chart_type": "bar",
            "source_item_ids": ["q_ops_rates"],
            "x_field": "device_type",
            "y_fields": [
                {"field": "online_rate", "name": "在线率(%)"},
                {"field": "complete_rate", "name": "完整率(%)"},
                {"field": "effective_rate", "name": "有效率(%)"},
            ],
        },
        gathered_data={"q_ops_rates": rows},
    )
    assert rendered is not None
    assert rendered["spec"].get("grouped") is True


def test_alert_table_column_keys_include_seq() -> None:
    rows = [
        {
            "seq": 1,
            "device_name": "F1",
            "device_type": "GNSS",
            "area_name": "朝阳区",
            "ss_value": "1.2",
            "content": "超限",
        }
    ]
    md, tbl = render_configured_table(
        {
            "id": "t_device_alert",
            "title": "设备数据告警情况统计表",
            "source_item_ids": ["q_alert_list"],
            "columns": ["序号", "标编号", "设备类型", "位置", "实时值", "告警内容"],
            "column_keys": ["seq", "device_name", "device_type", "area_name", "ss_value", "content"],
        },
        gathered_data={"q_alert_list": rows},
    )
    assert "序号" in md
    assert tbl["rows"][0]["序号"] == 1


def test_sql_files_on_disk() -> None:
    sql_dir = (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "analysis_agent_reports"
        / "sql"
    )
    assert (sql_dir / "om_ops_offline_list.sql").is_file()
    assert (sql_dir / "om_alert_count_by_type.sql").is_file()
