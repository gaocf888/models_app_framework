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
    # 负=沉降、正=回弹（与 settle_text / 季报表图一致）
    {"project_name": "F42(东坝)", "lon": 116.55, "lat": 39.95, "delta_mm": -12.5},
    {"project_name": "回龙观", "lon": 116.35, "lat": 40.08, "delta_mm": 3.2},
    {"project_name": "零变", "lon": 116.40, "lat": 39.90, "delta_mm": 0.0},
    {"project_name": "越界", "lon": 1.0, "lat": 1.0, "delta_mm": -9.0},
    {"project_name": "缺坐标", "delta_mm": -1.0},
]


def setup_function() -> None:
    clear_slot_cache()


def test_display_station_name_inner_parens() -> None:
    assert display_station_name("F42(东坝)") == "东坝"
    assert display_station_name("回龙观") == "回龙观"
    assert display_station_name("") == ""


def test_fill_for_delta_sign_matches_report() -> None:
    # 负值=沉降（红），正值=回弹（绿），与 settle_text / 季报表图一致
    assert fill_for_delta(-12.5) == "#e74c3c"
    assert fill_for_delta(3.2) == "#2ecc71"
    assert fill_for_delta(0.0) == "#95a5a6"


def test_dissolve_city_outline_merges_adjacent_districts() -> None:
    shapely = pytest.importorskip("shapely")
    from shapely.geometry import box

    from app.analysis_agent.renderers.offline_map import _dissolve_city_outline

    a = box(116.0, 39.5, 116.5, 40.0)
    b = box(116.5, 39.5, 117.0, 40.0)
    merged = _dissolve_city_outline([a, b])
    assert merged is not None
    assert merged.geom_type == "Polygon"
    assert merged.area == pytest.approx(a.area + b.area, rel=1e-3)


def test_city_outline_rings_without_shapely() -> None:
    pytest.importorskip("matplotlib")
    from app.analysis_agent.renderers.offline_map import _city_outline_rings_by_raster

    # 两个相接矩形：栅格溶解后外轮廓应覆盖 [0,2]x[0,1]
    left = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
    right = [(1.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 0.0)]
    rings = _city_outline_rings_by_raster([left, right], res_deg=0.05)
    assert rings
    xs = [p[0] for p in rings[0]]
    ys = [p[1] for p in rings[0]]
    assert min(xs) <= 0.15 and max(xs) >= 1.85
    assert min(ys) <= 0.15 and max(ys) >= 0.85
    # 外轮廓点数应明显少于两区边界之和，且不是简单两区各自外环
    assert len(rings) == 1


def test_basemap_rings_include_pink_city_outline() -> None:
    from app.analysis_agent.renderers import offline_map as om

    assets = om.default_map_assets_dir()
    if not list((assets / "shapefile").glob("*.shp")):
        pytest.skip("map shapefile missing")
    om._basemap_cache = None
    bm = om._load_basemap(assets)
    assert bm is not None
    if bm.get("mode") == "rings":
        assert bm.get("city_rings"), "city_rings must be non-empty for pink outline"
        assert len(bm["city_rings"][0]) >= 20
        by_name = {str(d.get("name") or ""): d for d in (bm.get("districts") or [])}
        miyun = by_name.get("密云区")
        assert miyun is not None
        xs = [p[0] for r in (miyun.get("exteriors") or []) for p in r]
        ys = [p[1] for r in (miyun.get("exteriors") or []) for p in r]
        assert xs and max(xs) - min(xs) > 0.3, "密云主面不得被小岛环替换"
        assert max(ys) > 40.5
        city = bm["city_rings"][0]
        assert max(p[0] for p in city) >= 117.3, "市界应覆盖密云东部"
    elif bm.get("mode") == "gpd":
        assert bm.get("city_union") is not None


def test_bar_scale_not_crushed_by_outlier() -> None:
    from app.analysis_agent.renderers.offline_map import (
        _robust_bar_ref_mm,
        _scaled_bar_height,
        _station_layout_metrics,
    )

    stations = [
        {"val": -10.0},
        {"val": -8.0},
        {"val": 3.0},
        {"val": 58.62},  # 离群回弹
    ]
    ref = _robust_bar_ref_mm(stations)
    assert ref < 30.0
    assert ref >= 8.0
    _lon, lat_span, hs, _bw, _fs = _station_layout_metrics(
        stations, 115.4, 117.5, 39.4, 41.1
    )
    h10 = abs(_scaled_bar_height(-10.0, hs, lat_span))
    h58 = abs(_scaled_bar_height(58.62, hs, lat_span))
    # 10mm 柱应接近参照可视高度，且明显高于旧逻辑下的「几个像素」
    assert h10 >= lat_span * 0.025
    # 极值封顶，不能无限拉长
    assert h58 <= lat_span * 0.065 + 1e-9


def test_map_chrome_helpers_callable() -> None:
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt

    from app.analysis_agent.renderers.offline_map import _draw_map_chrome, _ensure_mpl

    assert _ensure_mpl() is True
    fig, ax = plt.subplots(1, 1, figsize=(4, 4))
    ax.set_xlim(116.0, 117.0)
    ax.set_ylim(39.5, 40.5)
    _draw_map_chrome(ax, 116.0, 117.0, 39.5, 40.5)
    assert len(ax.patches) > 0
    plt.close(fig)


def test_stations_from_layer0_rows_plot_val_equals_delta() -> None:
    """val 与 delta_mm 同号：负沉降柱向下，正回弹柱向上；标注用站码。"""
    stations = stations_from_layer0_rows(SAMPLE_ROWS)
    assert [s["id"] for s in stations] == ["F42", "回龙观", "零变"]
    by_id = {s["id"]: s for s in stations}
    assert by_id["F42"]["val"] == pytest.approx(-12.5)
    assert by_id["回龙观"]["val"] == pytest.approx(3.2)
    assert by_id["零变"]["val"] == pytest.approx(0.0)
    assert by_id["F42"]["delta_mm"] == pytest.approx(-12.5)


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


def test_station_group_label_only_lr_then_shift_lonlat() -> None:
    from app.analysis_agent.renderers.offline_map import (
        _centered_text_box,
        _choose_district_center,
        _choose_station_group,
        _plan_station_labels,
        _rect_overlap,
        _station_label_rect,
        _station_plan_boxes,
    )

    lon_span, lat_span = 1.6, 1.8
    bar_width = lon_span * 0.007
    height_scale = (lat_span * 0.03) / 2.0
    pad = lon_span * 0.0015
    reserved = [_centered_text_box(116.66, 39.91, "通州区", lon_span, lat_span)]
    px, py, side, label, bar = _choose_station_group(
        116.68,
        39.91,
        val=-0.11,
        sides=("right", "left"),
        val_text="0.11",
        name_text="大稿",
        bar_width=bar_width,
        height_scale=height_scale,
        lon_span=lon_span,
        lat_span=lat_span,
        fontsize=7,
        occupied=reserved,
    )
    assert side in {"left", "right"}
    assert not any(_rect_overlap(box, reserved[0], pad) for box in (label, bar))

    px2, py2, side2, label2, bar2 = _choose_station_group(
        116.68,
        39.91,
        val=0.03,
        sides=("right", "left"),
        val_text="-0.03",
        name_text="永顺",
        bar_width=bar_width,
        height_scale=height_scale,
        lon_span=lon_span,
        lat_span=lat_span,
        fontsize=7,
        occupied=reserved + [label, bar],
    )
    assert side2 in {"left", "right"}
    assert not any(
        _rect_overlap(a, b, pad)
        for a in (label, bar)
        for b in (label2, bar2)
    )

    left_block = (116.5 - 0.18, 39.985, 116.5 - 0.008, 40.015)
    right_block = (116.5 + 0.008, 39.985, 116.5 + 0.18, 40.015)
    sx, sy, sside, slabel, sbar = _choose_station_group(
        116.5,
        40.0,
        val=0.0,
        sides=("right", "left"),
        val_text="0.00",
        name_text="密站点",
        bar_width=bar_width,
        height_scale=height_scale,
        lon_span=lon_span,
        lat_span=lat_span,
        fontsize=7,
        occupied=[left_block, right_block],
    )
    assert sside in {"left", "right"}
    assert abs(sx - 116.5) > 1e-6 or abs(sy - 40.0) > 1e-6
    assert not any(
        _rect_overlap(box, blk, pad)
        for box in (slabel, sbar)
        for blk in (left_block, right_block)
    )

    blocker = _station_label_rect(
        116.5, 40.0, side="right", extra=0.0,
        val_text="1.00", name_text="占位", bar_width=0.01,
        lon_span=lon_span, lat_span=lat_span, fontsize=9,
    )
    cx, cy, dbox = _choose_district_center(116.5, 40.0, "朝阳区", lon_span, lat_span, [blocker])
    assert not _rect_overlap(dbox, blocker, pad)

    plan = _plan_station_labels(
        [
            {"id": "甲", "lon": 116.5, "lat": 40.0, "val": -1.0, "delta_mm": -1.0},
            {"id": "乙", "lon": 116.5, "lat": 40.0, "val": -2.0, "delta_mm": -2.0},
        ],
        115.5,
        117.1,
        39.1,
        40.9,
    )
    assert all(item[3] in {"left", "right"} for item in plan)
    first = _station_plan_boxes(plan[:1])
    second = _station_plan_boxes(plan[1:])
    assert not any(_rect_overlap(a, b, pad) for a in first for b in second)


def test_shp_lite_length_and_area() -> None:
    from app.analysis_agent.renderers.shp_lite import polyline_length_m, ring_area_m2

    # ~111.32 km per degree lat
    assert polyline_length_m([(116.0, 40.0), (116.0, 40.5)]) == pytest.approx(111320.0 * 0.5, rel=0.02)
    square = [(0.0, 0.0), (0.01, 0.0), (0.01, 0.01), (0.0, 0.01), (0.0, 0.0)]
    assert ring_area_m2(square) > 5e5


def test_daily_plain_monitoring_has_no_map_chart() -> None:
    spec = load_report_spec("subsidence_daily")
    assert spec is not None
    assert not any(str(c.get("id") or "").startswith("c_plain_map") for c in spec.charts)


def test_quarterly_city_plain_keeps_map_chart() -> None:
    spec = load_report_spec("subsidence_quarterly")
    assert spec is not None
    assert any(c.get("id") == "c_city_map" for c in spec.charts)
