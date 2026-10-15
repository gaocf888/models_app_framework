"""离线沉降分布图：Δ 符号、站点过滤、chart payload。"""

from __future__ import annotations

import pytest

from app.analysis_agent.renderers.charts_extra import chart_from_config
from app.analysis_agent.renderers.configured_viz import prepare_chapter_viz
from app.analysis_agent.renderers.offline_map import (
    display_station_name,
    fill_for_delta,
    stations_from_layer0_rows,
)
from app.analysis_agent.report_spec import load_report_spec
from app.analysis_agent.slots.registry import clear_slot_cache


SAMPLE_ROWS = [
    {"project_name": "F42(东坝)", "lon": 116.55, "lat": 39.95, "delta_mm": 12.5},
    {"project_name": "回龙观", "lon": 116.35, "lat": 40.08, "delta_mm": -3.2},
    {"project_name": "零变", "lon": 116.40, "lat": 39.90, "delta_mm": 0.0},
    {"project_name": "越界", "lon": 1.0, "lat": 1.0, "delta_mm": 9.0},
    {"project_name": "缺坐标", "delta_mm": 1.0},
]


def setup_function() -> None:
    clear_slot_cache()


def test_display_station_name_inner_parens() -> None:
    assert display_station_name("F42(东坝)") == "东坝"
    assert display_station_name("回龙观") == "回龙观"
    assert display_station_name("") == ""


def test_fill_for_delta_sign_matches_report() -> None:
    assert fill_for_delta(12.5) == "#e74c3c"
    assert fill_for_delta(-3.2) == "#2ecc71"
    assert fill_for_delta(0.0) == "#95a5a6"


def test_stations_from_layer0_rows_plot_val_negates_delta() -> None:
    stations = stations_from_layer0_rows(SAMPLE_ROWS)
    assert [s["id"] for s in stations] == ["东坝", "回龙观", "零变"]
    by_id = {s["id"]: s for s in stations}
    assert by_id["东坝"]["val"] == pytest.approx(-12.5)
    assert by_id["回龙观"]["val"] == pytest.approx(3.2)
    assert by_id["零变"]["val"] == pytest.approx(0.0)
    assert by_id["东坝"]["delta_mm"] == pytest.approx(12.5)


def test_chart_from_config_map_png_or_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("matplotlib")
    ch = chart_from_config(
        chart_id="c_city_map",
        chart_type="map_placeholder",
        title="全市平原沉降分布图",
        rows=SAMPLE_ROWS,
    )
    assert ch is not None
    assert ch["id"] == "c_city_map"
    assert len(ch["spec"]["stations"]) == 3
    if ch["chart_type"] == "map_image":
        assert ch["placeholder"] is False
        assert ch["image_mime"] == "image/png"
        assert ch["image_base64"]
        raw = __import__("base64").b64decode(ch["image_base64"])
        assert raw.startswith(b"\x89PNG")
        assert ch["spec"]["renderer"] in {"offline_gis", "offline_points"}
    else:
        assert ch["chart_type"] == "map_placeholder"
        assert ch["placeholder"] is True

    monkeypatch.setenv("ANALYSIS_AGENT_MAP_RENDER_ENABLED", "false")
    disabled = chart_from_config(
        chart_id="c_city_map",
        chart_type="map_placeholder",
        title="全市平原沉降分布图",
        rows=SAMPLE_ROWS,
    )
    assert disabled is not None
    assert disabled["chart_type"] == "map_placeholder"
    assert disabled["placeholder"] is True
    assert disabled["spec"]["renderer"] == "disabled"


def test_chart_from_config_map_empty_rows_placeholder() -> None:
    ch = chart_from_config(
        chart_id="c_empty",
        chart_type="map_placeholder",
        title="空",
        rows=[],
    )
    assert ch is not None
    assert ch["chart_type"] == "map_placeholder"
    assert ch["placeholder"] is True
    assert ch["spec"]["renderer"] in {"no_stations", "disabled", "matplotlib_missing"}


def test_prepare_chapter_viz_emits_map() -> None:
    out = prepare_chapter_viz(
        chapter_id="city_plain",
        report_tables=[],
        report_charts=[
            {
                "id": "c_city_map",
                "title": "全市平原沉降分布图",
                "chart_type": "map_placeholder",
                "source_item_ids": ["q_layer0"],
                "attach_to_chapter": "city_plain",
            }
        ],
        gathered_data={"q_layer0": SAMPLE_ROWS},
    )
    assert len(out["charts"]) == 1
    ch = out["charts"][0]
    assert ch["configured"] is True
    assert ch["chart_type"] in {"map_image", "map_placeholder"}


def test_daily_plain_monitoring_has_map_chart() -> None:
    spec = load_report_spec("subsidence_daily")
    assert spec is not None
    maps = [c for c in spec.charts if c.get("chart_type") == "map_placeholder"]
    assert any(c.get("id") == "c_plain_map" for c in maps)
    assert any(c.get("attach_to_chapter") == "plain_monitoring" for c in maps)
