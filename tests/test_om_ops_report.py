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


def test_om_sql_templates_exist_and_bind_snapshot() -> None:
    for name in (
        "om_ops_rates_by_type.sql",
        "om_ops_offline_list.sql",
        "om_alert_summary.sql",
        "om_alert_count_by_type.sql",
        "om_alert_list.sql",
    ):
        sql = load_sql_template(name)
        assert "om_result." in sql
        assert ":snapshot_time" in sql
        assert "max(snapshot_time)" not in sql
        assert "now()" not in sql.lower()


def test_om_rates_sql_aliases_rate_columns() -> None:
    sql = load_sql_template("om_ops_rates_by_type.sql")
    assert "c.rate AS complete_rate" in sql
    assert "e.rate AS effective_rate" in sql
    assert "c.complete_rate" not in sql
    assert "e.effective_rate" not in sql


def test_om_offline_sql_filters_status_and_days() -> None:
    sql = load_sql_template("om_ops_offline_list.sql")
    assert "status = 0" in sql
    assert "offline_days" in sql
    assert "t_om_offline_device_list" in sql
    assert "CAST(:snapshot_time AS timestamp) - o.data_collect_time" in sql


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


def test_layer0_audit_facts_include_districts_and_extremes() -> None:
    from app.analysis_agent.renderers.section_data import build_audit_facts

    text = build_audit_facts(
        {
            "q_layer0": [
                {"area": "朝阳区", "project_name": "F29(东窑)", "delta_mm": -12.5},
                {"area": "海淀区", "project_name": "回龙观", "delta_mm": 3.2},
                {"area": "朝阳区", "project_name": "F1(王四营）", "delta_mm": -1.0},
            ]
        },
        "日报",
        slot_id="plain_monitoring",
    )
    assert "全市" in text
    assert "朝阳区" in text
    assert "以沉降为主" in text
    assert "东窑" in text or "F29" in text
    assert "分行政区" in text
    assert "Δ" not in text
    assert "平均" not in text
    assert "初−末" not in text


def test_plain_monitoring_narrative_no_delta_no_mean() -> None:
    from app.analysis_agent.deterministics.plain_narrative import (
        render_plain_monitoring_narrative,
    )

    md = render_plain_monitoring_narrative(
        {
            "q_layer0": [
                {"area": "朝阳区", "project_name": "F29(东窑)", "delta_mm": -12.5},
                {"area": "朝阳区", "project_name": "F17(沟渠庄)", "delta_mm": -8.1},
                {"area": "海淀区", "project_name": "回龙观", "delta_mm": 3.2},
                {"area": "通州区", "project_name": "张家湾", "delta_mm": 1.1},
            ]
        }
    )
    assert "Δ" not in md
    assert "初−末" not in md and "初-末" not in md
    assert "平均沉降" not in md
    assert "朝阳区" in md and "以沉降为主" in md
    assert "海淀区" in md and "以回弹为主" in md
    assert "12.50毫米" in md
    assert "8.10毫米" in md
    assert "3.20毫米" in md
    assert "1.10毫米" in md
    assert "沉降最大" in md and "次之" in md
    assert "回弹最大" in md


def test_ops_narrative_covers_every_rate_type() -> None:
    from app.analysis_agent.deterministics.ops_narrative import render_station_ops_narrative

    gathered = {
        "q_ops_rates": [
            {
                "device_type": "GNSS",
                "total": 92,
                "online": 92,
                "offline": 0,
                "online_rate": 100.0,
                "complete_rate": 82.07,
                "effective_rate": 82.07,
            },
            {
                "device_type": "光纤自动解调仪",
                "total": 3,
                "online": 3,
                "offline": 0,
                "online_rate": 100.0,
                "complete_rate": 80.56,
                "effective_rate": 80.56,
            },
            {
                "device_type": "地下水位计",
                "total": 302,
                "online": 302,
                "offline": 0,
                "online_rate": 100.0,
                "complete_rate": 99.89,
                "effective_rate": 99.65,
            },
            {
                "device_type": "静力水准仪",
                "total": 716,
                "online": 716,
                "offline": 0,
                "online_rate": 100.0,
                "complete_rate": 95.82,
                "effective_rate": 88.62,
            },
            {
                "device_type": "孔隙水压力计",
                "total": 289,
                "online": 289,
                "offline": 0,
                "online_rate": 100.0,
                "complete_rate": 99.45,
                "effective_rate": 96.94,
            },
            {
                "device_type": "气象站",
                "total": 34,
                "online": 34,
                "offline": 0,
                "online_rate": 100.0,
                "complete_rate": 99.54,
                "effective_rate": 99.54,
            },
        ],
        "q_ops_offline_list": [],
    }
    md = render_station_ops_narrative(gathered)
    for name in ("GNSS", "光纤自动解调仪", "地下水位计", "静力水准仪", "孔隙水压力计", "气象站"):
        assert name in md
    assert "气象站共计设备34套" in md
    assert "其他仪器类型本批暂无运维汇总" not in md
    assert md.count("具体情况详见图1") == 6


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
    assert ch["spec"].get("categories") == ["GNSS", "静力水准仪"]
    assert ch["spec"]["series"][0].get("values") == [95.0, 100.0]
    assert ch["spec"]["series"][0].get("data") == [95.0, 100.0]
    assert (ch["spec"].get("option") or {}).get("series")
    assert ch["spec"]["option"]["series"][0]["data"] == [95.0, 100.0]
    assert not ch.get("image_base64")

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


def test_alert_table_truncates_over_100_with_note() -> None:
    rows = [
        {
            "seq": i,
            "device_name": f"D{i}",
            "device_type": "GNSS",
            "area_name": "朝阳区",
            "ss_value": "1",
            "content": "超限",
        }
        for i in range(1, 106)
    ]
    md, tbl = render_configured_table(
        {
            "id": "t_device_alert",
            "title": "设备数据告警情况统计表",
            "source_item_ids": ["q_alert_list"],
            "columns": ["序号", "标编号", "设备类型", "位置", "实时值", "告警内容"],
            "column_keys": ["seq", "device_name", "device_type", "area_name", "ss_value", "content"],
            "max_rows": 100,
        },
        gathered_data={"q_alert_list": rows},
    )
    assert tbl is not None
    assert len(tbl["rows"]) == 100
    assert tbl.get("truncated") is True
    assert tbl.get("row_count") == 105
    assert "仅展示前 100 条" in (tbl.get("note") or "")
    assert "仅展示前 100 条" in md


def test_sql_files_on_disk() -> None:
    sql_dir = (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "analysis_agent_reports"
        / "sql"
    )
    assert (sql_dir / "om_ops_offline_list.sql").is_file()
    assert (sql_dir / "om_alert_count_by_type.sql").is_file()
    assert (sql_dir / "om_snapshot_pick.sql").is_file()
