"""季报对外展示：符号、平均、站名、附表列、禁公式。"""

from __future__ import annotations

from app.analysis_agent.deterministics.fcb_compress import rank_by_area
from app.analysis_agent.deterministics.settle_text import (
    format_mm,
    is_rebound,
    is_sink,
    strip_formula_text,
)
from app.analysis_agent.deterministics.site_display import attach_site_display, load_site_displays
from app.analysis_agent.renderers.configured_viz import render_configured_table
from app.analysis_agent.renderers.section_data import sanitize_report_narrative


def test_sign_matches_word_template() -> None:
    assert is_sink(-8.77)
    assert is_rebound(3.02)
    assert format_mm(-8.77) == "8.77毫米"
    assert format_mm(-8.77, signed=True) == "-8.77"


def test_strip_formula_from_narrative() -> None:
    raw = "本期 Δ = total_settle(窗初) − total_settle(窗末)，delta_mm=-1.2。"
    out = strip_formula_text(raw)
    assert "Δ" not in out
    assert "total_settle" not in out
    assert "delta_mm" not in out
    assert "Δ" not in sanitize_report_narrative(raw)


def test_site_display_forty_two() -> None:
    rows = load_site_displays()
    assert len(rows) == 42
    extra = attach_site_display("F16(三村)", "顺义区")
    assert extra["annex_label"] == "高丽营镇三村F16"
    assert extra["station_code"] == "F16"
    assert "高丽营镇三村" in extra["display_clause"]


def test_rank_by_area_uses_average_and_drops_unknown() -> None:
    rows = rank_by_area(
        {
            "q_layer0": [
                {"area": "朝阳区", "delta_mm": -2.0},
                {"area": "朝阳区", "delta_mm": -4.0},
                {"area": "未知", "delta_mm": -99.0},
                {"area": "", "delta_mm": 1.0},
            ]
        }
    )
    assert [r["area"] for r in rows] == ["朝阳区"]
    assert rows[0]["avg_delta_mm"] == -3.0
    assert rows[0]["station_count"] == 2


def test_city_table_chinese_headers() -> None:
    md, tbl = render_configured_table(
        {
            "id": "t_city_area_rank",
            "title": "全市平原各行政区沉降统计表",
            "source_item_ids": ["q_area_rank"],
            "columns": ["行政区", "监测站/座", "平均沉降量/毫米", "最小", "最大"],
            "column_keys": ["area", "station_count", "avg_delta_mm", "min_delta_mm", "max_delta_mm"],
            "round_decimals": 2,
        },
        gathered_data={
            "q_area_rank": [
                {
                    "area": "朝阳区",
                    "station_count": 2,
                    "avg_delta_mm": -1.23456,
                    "min_delta_mm": -2.0,
                    "max_delta_mm": -0.5,
                }
            ]
        },
    )
    assert "行政区" in md
    assert "avg_delta_mm" not in md
    assert "-1.23" in md
    assert tbl["rows"][0]["平均沉降量/毫米"] == -1.23


def test_annex_ground_grouped_label() -> None:
    md, tbl = render_configured_table(
        {
            "id": "t_annex_ground",
            "title": "附表1",
            "source_item_ids": ["q_layer0"],
            "columns": ["监测点", "沉降量"],
            "column_keys": ["annex_label", "delta_mm"],
            "group_field": "area",
            "group_title_key": "annex_label",
            "round_decimals": 2,
            "unit_line": "单位：毫米",
        },
        gathered_data={
            "q_layer0": [
                {"area": "顺义区", "annex_label": "高丽营镇三村F16", "delta_mm": -8.771},
            ]
        },
    )
    assert "单位：毫米" in md
    assert "顺义区" in md
    assert "高丽营镇三村F16" in md
    assert "-8.77" in md
    assert "settle_start" not in md
    assert any(r.get("监测点") == "顺义区" for r in tbl["rows"])
