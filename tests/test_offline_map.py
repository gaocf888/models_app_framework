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
        assert ch["spec"]["renderer"] in {"offline_gis", "offline_shp", "offline_points"}
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


def test_resolve_cjk_uses_bundled_noto_serif_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("matplotlib")
    from app.analysis_agent.renderers import offline_map as om

    fonts = tmp_path / "fonts"
    fonts.mkdir()
    fake = fonts / "NotoSerifCJKsc-Regular.otf"
    fake.write_bytes(b"OTTO")
    om._cjk_font_searched = False
    om._cjk_font_file = None
    monkeypatch.setattr(om, "default_map_assets_dir", lambda: tmp_path)
    path = om.resolve_cjk_font_file()
    assert path is not None
    assert path.endswith("NotoSerifCJKsc-Regular.otf")
    om._cjk_font_searched = False
    om._cjk_font_file = None


def test_cjk_filename_accepts_noto_serif_sc() -> None:
    from app.analysis_agent.renderers.offline_map import _is_cjk_font_filename

    assert _is_cjk_font_filename("NotoSerifCJKsc-Regular.otf")
    assert _is_cjk_font_filename("NotoSansSC-Regular.otf")
    assert _is_cjk_font_filename("wqy-zenhei.ttc")


def test_ensure_mpl_does_not_request_simhei() -> None:
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    from app.analysis_agent.renderers import offline_map as om

    om._mpl_ready = False
    assert om._ensure_mpl() is True
    families = [str(x) for x in plt.rcParams["font.sans-serif"]]
    assert "SimHei" not in families


def test_map_png_uses_vector_basemap_when_shapefile_present() -> None:
    pytest.importorskip("matplotlib")
    from app.analysis_agent.renderers.offline_map import (
        default_map_assets_dir,
        render_subsidence_map_png,
    )

    shp_dir = default_map_assets_dir() / "shapefile"
    if not list(shp_dir.glob("*.shp")):
        pytest.skip("map shapefile missing")
    png, tag = render_subsidence_map_png(
        SAMPLE_ROWS,
        title="平原区沉降分布图",
    )
    assert png is not None
    assert png.startswith(b"\x89PNG")
    assert tag in {"offline_gis", "offline_shp"}


def test_shp_lite_reads_beijing_districts() -> None:
    from app.analysis_agent.renderers.offline_map import default_map_assets_dir
    from app.analysis_agent.renderers.shp_lite import first_shp, iter_shp_rings, read_dbf_rows

    shp = first_shp(default_map_assets_dir() / "shapefile")
    if shp is None:
        pytest.skip("map shapefile missing")
    recs = iter_shp_rings(shp, want_types=(5,))
    assert len(recs) == 16
    dbf = shp.with_suffix(".dbf")
    rows = read_dbf_rows(dbf)
    names = {str(r.get("district") or "") for r in rows}
    assert "大兴区" in names
    assert "朝阳区" in names


def test_legend_has_no_delta_parentheses() -> None:
    import inspect

    from app.analysis_agent.renderers.offline_map import _draw_legend

    src = inspect.getsource(_draw_legend)
    assert "地面回弹" in src
    assert "地面沉降" in src
    assert "Δ" not in src


def test_choose_cardinal_avoids_district_and_other_labels() -> None:
    from app.analysis_agent.renderers.offline_map import (
        _centered_text_box,
        _choose_cardinal_placement,
        _choose_district_center,
        _rect_overlap,
        _station_label_rect,
    )

    lon_span, lat_span = 1.6, 1.8
    reserved = [_centered_text_box(116.66, 39.91, "通州区", lon_span, lat_span)]
    side, _extra, box = _choose_cardinal_placement(
        116.68,
        39.91,
        sides=("right", "left", "up", "down"),
        val_text="0.11",
        name_text="大稿",
        bar_width=0.01,
        lon_span=lon_span,
        lat_span=lat_span,
        fontsize=7,
        occupied=reserved,
    )
    assert side in {"left", "right", "up", "down"}
    assert not _rect_overlap(box, reserved[0], lon_span * 0.0015)
    side2, _extra2, box2 = _choose_cardinal_placement(
        116.68,
        39.91,
        sides=("right", "left", "up", "down"),
        val_text="-0.03",
        name_text="永顺",
        bar_width=0.01,
        lon_span=lon_span,
        lat_span=lat_span,
        fontsize=7,
        occupied=reserved + [box],
    )
    assert side2 in {"left", "right", "up", "down"}
    assert not _rect_overlap(box, box2, lon_span * 0.0015)

    blocker = _station_label_rect(
        116.5, 40.0, side="right", extra=0.0,
        val_text="1.00", name_text="占位", bar_width=0.01,
        lon_span=lon_span, lat_span=lat_span, fontsize=9,
    )
    cx, cy, dbox = _choose_district_center(116.5, 40.0, "朝阳区", lon_span, lat_span, [blocker])
    assert not _rect_overlap(dbox, blocker, lon_span * 0.0015)


def test_shp_lite_length_and_area() -> None:
    from app.analysis_agent.renderers.shp_lite import polyline_length_m, ring_area_m2

    # ~111.32 km per degree lat
    assert polyline_length_m([(116.0, 40.0), (116.0, 40.5)]) == pytest.approx(111320.0 * 0.5, rel=0.02)
    square = [(0.0, 0.0), (0.01, 0.0), (0.01, 0.01), (0.0, 0.01), (0.0, 0.0)]
    assert ring_area_m2(square) > 5e5


def test_daily_plain_monitoring_has_map_chart() -> None:
    spec = load_report_spec("subsidence_daily")
    assert spec is not None
    maps = [c for c in spec.charts if c.get("chart_type") == "map_placeholder"]
    assert any(c.get("id") == "c_plain_map" for c in maps)
    assert any(c.get("attach_to_chapter") == "plain_monitoring" for c in maps)
