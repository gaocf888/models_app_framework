"""地降自动报告：离线矢量/栅格底图 + 层位 0 站点柱，输出 PNG。

Δ=初−末：Δ>0 下沉（红、柱向下），Δ<0 回弹（绿、柱向上），与 formulas.period_delta 一致。
有 geopandas/rasterio 且市界 shp 齐全时走 DEM+区县+水系；否则退化为经纬度散点图。
"""

from __future__ import annotations

import base64
import io
import os
import threading
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_COLOR_SINK = "#e74c3c"
_COLOR_REBOUND = "#2ecc71"
_COLOR_ZERO = "#95a5a6"

_basemap_lock = threading.Lock()
_basemap_cache: dict[str, Any] | None = None
_mpl_ready = False


def display_station_name(project_name: str) -> str:
    raw = (project_name or "").strip()
    if not raw:
        return ""
    if "(" in raw and raw.endswith(")"):
        inner = raw[raw.find("(") + 1 : -1].strip()
        if inner:
            return inner
    return raw


def fill_for_delta(delta_mm: float) -> str:
    if delta_mm > 0:
        return _COLOR_SINK
    if delta_mm < 0:
        return _COLOR_REBOUND
    return _COLOR_ZERO


def stations_from_layer0_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """只保留有 lon/lat/delta 的层位 0 行；val 为绘图用 −Δ（下沉向下）。"""
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            lon = float(row.get("lon"))
            lat = float(row.get("lat"))
            delta = float(row.get("delta_mm"))
        except (TypeError, ValueError):
            continue
        if not (70.0 < lon < 140.0 and 15.0 < lat < 55.0):
            continue
        name = display_station_name(str(row.get("project_name") or row.get("station_name") or ""))
        if not name:
            continue
        out.append(
            {
                "id": name,
                "lon": lon,
                "lat": lat,
                "delta_mm": delta,
                "val": -delta,
                "project_name": str(row.get("project_name") or ""),
            }
        )
    return out


def default_map_assets_dir() -> Path:
    override = (os.getenv("ANALYSIS_AGENT_MAP_ASSETS_DIR") or "").strip()
    if override:
        return Path(override)
    try:
        from app.core.config import get_app_config

        configured = (getattr(get_app_config().analysis_agent, "map_assets_dir", "") or "").strip()
        if configured:
            return Path(configured)
    except Exception:  # noqa: BLE001
        pass
    return Path(__file__).resolve().parents[3] / "configs" / "analysis_agent_reports" / "map_assets"


def map_render_enabled() -> bool:
    env = (os.getenv("ANALYSIS_AGENT_MAP_RENDER_ENABLED") or "").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False
    if env in {"1", "true", "yes", "on"}:
        return True
    try:
        from app.core.config import get_app_config

        return bool(getattr(get_app_config().analysis_agent, "map_render_enabled", True))
    except Exception:  # noqa: BLE001
        return True


def _ensure_mpl() -> bool:
    global _mpl_ready
    if _mpl_ready:
        return True
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt  # noqa: F401

        plt.rcParams["font.sans-serif"] = [
            "SimHei",
            "Microsoft YaHei",
            "WenQuanYi Zen Hei",
            "Noto Sans CJK SC",
            "Source Han Sans CN",
            "Arial Unicode MS",
            "DejaVu Sans",
            "sans-serif",
        ]
        plt.rcParams["axes.unicode_minus"] = False
        _mpl_ready = True
        return True
    except Exception:
        logger.warning("offline_map matplotlib unavailable", exc_info=True)
        return False


def _shapefile_complete(shp: Path) -> bool:
    if not shp.is_file():
        return False
    stem = shp.with_suffix("")
    return all((stem.with_suffix(ext)).is_file() for ext in (".shp", ".shx", ".dbf"))


def _try_gis_imports() -> bool:
    try:
        import geopandas  # noqa: F401
        import rasterio  # noqa: F401
        import shapely  # noqa: F401

        return True
    except Exception:
        return False


def _load_basemap(assets: Path) -> dict[str, Any] | None:
    """缓存区县界 / DEM 阴影 / 水系。缺 GIS 库或市界则 None。"""
    global _basemap_cache
    key = str(assets.resolve()) if assets.exists() else ""
    with _basemap_lock:
        if _basemap_cache is not None and _basemap_cache.get("key") == key:
            return _basemap_cache.get("data")
        data = _load_basemap_uncached(assets)
        _basemap_cache = {"key": key, "data": data}
        return data


def _is_wgs84(crs: Any) -> bool:
    if crs is None:
        return False
    try:
        return crs.to_epsg() == 4326
    except Exception:
        return str(crs).upper() in ("EPSG:4326", "WGS84")


def _load_dem_hillshade(path: Path) -> tuple[Any, tuple[float, float, float, float]] | None:
    import numpy as np
    import rasterio
    from matplotlib.colors import LightSource
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    with rasterio.open(path) as src:
        nodata = src.nodata
        src_transform = src.transform
        src_crs = src.crs
        src_data = src.read(1)
        if _is_wgs84(src_crs):
            dem = src_data.astype(np.float32)
            transform = src_transform
        else:
            transform, width, height = calculate_default_transform(
                src_crs, "EPSG:4326", src.width, src.height, *src.bounds
            )
            dem = np.full((height, width), np.nan, dtype=np.float32)
            reproject(
                source=src_data,
                destination=dem,
                src_transform=src_transform,
                src_crs=src_crs,
                dst_transform=transform,
                dst_crs="EPSG:4326",
                resampling=Resampling.bilinear,
                src_nodata=nodata,
                dst_nodata=np.nan,
            )
            nodata = np.nan
    dem = dem.astype(np.float32, copy=False)
    invalid = ~np.isfinite(dem)
    if nodata is not None and np.isfinite(nodata):
        invalid |= np.isclose(dem, nodata)
    invalid |= (dem < -1000) | (dem > 9000)
    dem[invalid] = np.nan
    if np.all(np.isnan(dem)):
        return None
    west, south, east, north = rasterio.transform.array_bounds(dem.shape[0], dem.shape[1], transform)
    extent = (west, east, south, north)
    fill_val = np.nanmedian(dem)
    dem_filled = np.where(np.isnan(dem), fill_val, dem)
    lat_mid = (south + north) / 2.0
    dx = abs(transform.a) * 111320.0 * np.cos(np.deg2rad(lat_mid))
    dy = abs(transform.e) * 111320.0
    ls = LightSource(azdeg=315, altdeg=45)
    vert_exag = 4.0
    e_dy, e_dx = np.gradient(vert_exag * dem_filled, -dy, dx)
    normal = np.empty(dem_filled.shape + (3,), dtype=np.float32)
    normal[..., 0] = -e_dx
    normal[..., 1] = -e_dy
    normal[..., 2] = 1.0
    mag = np.sqrt(np.sum(normal * normal, axis=-1, keepdims=True))
    mag = np.maximum(mag, 1e-12)
    normal /= mag
    intensity = normal.dot(ls.direction.astype(np.float32))
    flat = float(ls.direction[2])
    hs = np.clip(intensity / max(flat, 1e-6), 0.0, 1.0)
    hs = np.where(hs >= 0.92, 1.0, hs)
    hs = np.where(np.isnan(dem), np.nan, hs)
    return hs.astype(np.float32), extent


def _load_basemap_uncached(assets: Path) -> dict[str, Any] | None:
    shp = assets / "shapefile" / "北京市.shp"
    if not _shapefile_complete(shp) or not _try_gis_imports():
        logger.info("offline_map skip GIS basemap shp_ok=%s gis=%s", _shapefile_complete(shp), _try_gis_imports())
        return None
    import geopandas as gpd
    from shapely.ops import unary_union

    beijing = gpd.read_file(shp).to_crs(epsg=4326)
    city_union = unary_union(beijing.geometry)
    hillshade = None
    dem_extent = None
    dem_path = assets / "dem" / "beijing.tif"
    if dem_path.is_file():
        try:
            loaded = _load_dem_hillshade(dem_path)
            if loaded:
                hillshade, dem_extent = loaded
        except Exception:
            logger.warning("offline_map DEM hillshade failed", exc_info=True)
    water_areas = None
    waterways = None
    wa = assets / "water_area" / "gis_osm_water_a_free_1.shp"
    ww = assets / "waterway" / "gis_osm_waterways_free_1.shp"
    try:
        if _shapefile_complete(wa):
            water_areas = gpd.read_file(wa).to_crs(epsg=4326)
            water_areas = water_areas[water_areas["fclass"].isin(["water", "riverbank", "reservoir"])]
            area_m2 = water_areas.to_crs(epsg=3857).geometry.area
            water_areas = water_areas[area_m2 >= 5e4]
            water_areas = _clip_to_polygon(water_areas, city_union)
    except Exception:
        logger.warning("offline_map water_area skipped", exc_info=True)
        water_areas = None
    try:
        if _shapefile_complete(ww):
            waterways = gpd.read_file(ww).to_crs(epsg=4326)
            waterways = waterways[waterways["fclass"] == "river"]
            waterways = _clip_to_polygon(waterways, city_union)
            waterways = _filter_major_named_rivers(waterways, min_total_km=50.0)
    except Exception:
        logger.warning("offline_map waterway skipped", exc_info=True)
        waterways = None
    name_col = "district" if "district" in beijing.columns else None
    if name_col is None:
        for cand in ("NAME", "name", "区县", "XZQMC"):
            if cand in beijing.columns:
                name_col = cand
                break
    return {
        "beijing": beijing,
        "city_union": city_union,
        "hillshade": hillshade,
        "dem_extent": dem_extent,
        "water_areas": water_areas,
        "waterways": waterways,
        "name_col": name_col,
    }


def _clip_to_polygon(gdf: Any, clip_geom: Any) -> Any:
    if gdf is None or gdf.empty:
        return gdf
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    hits = gdf[gdf.intersects(clip_geom)]
    if hits.empty:
        return hits
    hits = hits.copy()
    hits["geometry"] = hits.geometry.intersection(clip_geom)
    return hits[~hits.geometry.is_empty]


def _filter_major_named_rivers(gdf: Any, min_total_km: float = 50.0) -> Any:
    if gdf is None or gdf.empty or "name" not in gdf.columns:
        return gdf
    named = gdf[gdf["name"].fillna("").str.strip() != ""].copy()
    if named.empty:
        return named
    named["len_m"] = named.to_crs(epsg=3857).geometry.length
    keep_names = named.groupby("name")["len_m"].sum().loc[lambda s: s >= min_total_km * 1000.0].index
    return named[named["name"].isin(keep_names)].drop(columns=["len_m"])


def _iter_polygons(geom: Any):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield geom
    elif geom.geom_type == "MultiPolygon":
        yield from geom.geoms


def _iter_lines(geom: Any):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == "LineString":
        yield geom
    elif geom.geom_type == "MultiLineString":
        yield from geom.geoms
    elif geom.geom_type == "GeometryCollection":
        for part in geom.geoms:
            yield from _iter_lines(part)


def _polygon_to_path(geom: Any) -> Any:
    import numpy as np
    from matplotlib.path import Path as MplPath

    verts, codes = [], []
    for ring in (geom.exterior, *geom.interiors):
        coords = np.asarray(ring.coords)
        if len(coords) < 3:
            continue
        verts.append(coords)
        ring_codes = np.full(len(coords), MplPath.LINETO, dtype=np.uint8)
        ring_codes[0] = MplPath.MOVETO
        ring_codes[-1] = MplPath.CLOSEPOLY
        codes.append(ring_codes)
    if not verts:
        return None
    return MplPath(np.concatenate(verts), np.concatenate(codes))


def _plot_water_areas(ax: Any, gdf: Any, facecolor: str, zorder: float) -> None:
    from matplotlib.patches import PathPatch

    for geom in gdf.geometry:
        for poly in _iter_polygons(geom):
            path = _polygon_to_path(poly)
            if path is None:
                continue
            ax.add_patch(
                PathPatch(
                    path,
                    facecolor=facecolor,
                    edgecolor="none",
                    linewidth=0,
                    alpha=0.88,
                    zorder=zorder,
                    transform=ax.transData,
                )
            )


def _plot_waterways(ax: Any, gdf: Any, zorder: float) -> None:
    width_by_class = {"river": 1.15, "canal": 0.85, "stream": 0.45}
    for _, row in gdf.iterrows():
        fclass = str(row.get("fclass", "stream"))
        lw = width_by_class.get(fclass, 0.5)
        for line in _iter_lines(row.geometry):
            xs, ys = line.xy
            ax.plot(
                xs,
                ys,
                color="#9dcee8",
                linewidth=lw,
                solid_capstyle="round",
                solid_joinstyle="round",
                zorder=zorder,
                alpha=0.95,
            )


def _plot_boundary(ax: Any, geom: Any, color: str, linewidth: float, zorder: float, linestyle: Any = "-") -> None:
    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
    else:
        return
    for poly in polys:
        xs, ys = poly.exterior.xy
        ax.plot(xs, ys, color=color, linewidth=linewidth, zorder=zorder, linestyle=linestyle, solid_capstyle="round")
        for hole in poly.interiors:
            hxs, hys = hole.xy
            ax.plot(hxs, hys, color=color, linewidth=max(linewidth - 0.3, 0.4), zorder=zorder, linestyle=linestyle)


def _draw_stations(ax: Any, stations: list[dict[str, Any]], minx: float, maxx: float, miny: float, maxy: float) -> None:
    import matplotlib.patches as mpatches
    from matplotlib.font_manager import FontProperties
    from matplotlib.offsetbox import AnnotationBbox, DrawingArea, TextArea, VPacker
    from matplotlib.textpath import TextPath

    lon_span = max(maxx - minx, 1e-6)
    lat_span = max(maxy - miny, 1e-6)
    max_abs = max((abs(float(s["val"])) for s in stations), default=1.0) or 1.0
    height_scale = (lat_span * 0.03) / max_abs
    bar_width = lon_span * 0.007
    fontsize = 7 if len(stations) > 24 else 8

    def _font():
        return FontProperties(family="SimHei", size=fontsize)

    def _text_width(text: str) -> float:
        try:
            return float(TextPath((0, 0), str(text), size=fontsize, prop=_font()).get_extents().width)
        except Exception:
            return max(fontsize * sum(0.95 if ord(ch) > 127 else 0.5 for ch in str(text)), fontsize)

    def _hline(width_pt: float):
        width_pt = max(float(width_pt), 0.8)
        da = DrawingArea(width_pt, 0.72, 0, 0)
        da.add_artist(mpatches.Rectangle((0, 0), width_pt, 0.72, facecolor="#222222", edgecolor="none", linewidth=0))
        return da

    def _label_box(val_text: str, name_text: str, align: str):
        tp = dict(size=fontsize, color="#111111")
        line_w = max(_text_width(val_text), _text_width(name_text))
        headed = VPacker(
            children=[TextArea(val_text, textprops=tp), _hline(line_w)],
            align=align,
            pad=0,
            sep=0,
        )
        return VPacker(children=[headed, TextArea(name_text, textprops=tp)], align=align, pad=0, sep=2.0)

    for s in stations:
        x, y = float(s["lon"]), float(s["lat"])
        val = float(s["val"])
        color = fill_for_delta(float(s["delta_mm"]))
        bar_height = val * height_scale
        bar_bottom = y if val >= 0 else y + bar_height
        ax.add_patch(
            mpatches.Rectangle(
                (x - bar_width / 2.0, bar_bottom),
                bar_width,
                abs(bar_height) or lat_span * 0.002,
                facecolor=color,
                edgecolor="#222222",
                linewidth=0.4,
                zorder=5,
                clip_on=True,
            )
        )
        if x > minx + lon_span * 0.72:
            xy, align, box_align = (x - bar_width * 1.4, y), "right", (1.0, 0.5)
        else:
            xy, align, box_align = (x + bar_width * 1.4, y), "left", (0.0, 0.5)
        ab = AnnotationBbox(
            _label_box(f"{float(s['delta_mm']):.2f}", str(s["id"]), align),
            xy,
            xycoords="data",
            box_alignment=box_align,
            frameon=False,
            pad=0.05,
            zorder=5.5,
            annotation_clip=True,
        )
        ax.add_artist(ab)


def _draw_legend(ax: Any) -> None:
    import matplotlib.patches as mpatches
    from matplotlib.offsetbox import AnnotationBbox, DrawingArea, HPacker, TextArea, VPacker

    title = TextArea("图例", textprops=dict(size=11, color="#111111", fontweight="bold"))

    def swatch_row(color: str, label: str):
        da = DrawingArea(20, 13, 0, 0)
        da.add_artist(mpatches.Rectangle((1, 2), 16, 9, facecolor=color, edgecolor="#333333", linewidth=0.4))
        return HPacker(children=[da, TextArea(label, textprops=dict(size=10, color="#111111"))], align="center", pad=0, sep=6)

    pack = VPacker(
        children=[
            title,
            swatch_row(_COLOR_REBOUND, "地面回弹（Δ<0）"),
            swatch_row(_COLOR_SINK, "地面沉降（Δ>0）"),
            swatch_row("#9dcee8", "河流/水体"),
        ],
        align="left",
        pad=3,
        sep=5,
    )
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    margin = 0.07 * (y1 - y0)
    box = AnnotationBbox(
        pack,
        xy=(x1 - margin, y0 + margin),
        xycoords="data",
        box_alignment=(1.0, 0.0),
        frameon=True,
        pad=0.55,
        zorder=7,
        annotation_clip=False,
    )
    box.patch.set_facecolor("white")
    box.patch.set_edgecolor("#888888")
    box.patch.set_linewidth(0.8)
    box.patch.set_alpha(0.95)
    ax.add_artist(box)


def _fig_to_png(fig: Any) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="white")
    import matplotlib.pyplot as plt

    plt.close(fig)
    return buf.getvalue()


def _render_with_basemap(stations: list[dict[str, Any]], title: str, basemap: dict[str, Any]) -> bytes:
    import matplotlib.patheffects as pe
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    beijing = basemap["beijing"]
    fig, ax = plt.subplots(1, 1, figsize=(11, 9.5))
    ax.set_facecolor("white")
    hillshade, dem_extent = basemap.get("hillshade"), basemap.get("dem_extent")
    if hillshade is not None and dem_extent is not None:
        relief_cmap = LinearSegmentedColormap.from_list("relief_white", ["#8a8a8a", "#ffffff"])
        hs_plot = np.nan_to_num(hillshade, nan=1.0)
        ax.imshow(
            hs_plot,
            extent=dem_extent,
            cmap=relief_cmap,
            vmin=0.32,
            vmax=1.0,
            interpolation="bilinear",
            origin="upper",
            zorder=0,
            aspect="auto",
        )
    water_areas, waterways = basemap.get("water_areas"), basemap.get("waterways")
    if water_areas is not None and not water_areas.empty:
        _plot_water_areas(ax, water_areas, "#9dcee8", 1)
    if waterways is not None and not waterways.empty:
        _plot_waterways(ax, waterways, 1.2)
    for geom in beijing.geometry:
        _plot_boundary(ax, geom, color="#5a5a5a", linewidth=0.8, zorder=2, linestyle=(0, (4.0, 2.4)))
    _plot_boundary(ax, basemap["city_union"], color="#c026d3", linewidth=1.8, zorder=3)
    text_stroke = [pe.withStroke(linewidth=2.4, foreground="white", alpha=0.75)]
    name_col = basemap.get("name_col")
    if name_col:
        for geom, name in zip(beijing.geometry, beijing[name_col]):
            pt = geom.representative_point()
            ax.text(
                pt.x,
                pt.y,
                str(name),
                fontsize=9,
                color="#222222",
                ha="center",
                va="center",
                weight="bold",
                zorder=4,
                path_effects=text_stroke,
            )
    minx, miny, maxx, maxy = beijing.total_bounds
    _draw_stations(ax, stations, minx, maxx, miny, maxy)
    ax.set_title(title, fontsize=16, pad=16)
    pad_x = (maxx - minx) * 0.035
    pad_y = (maxy - miny) * 0.035
    ax.set_xlim(minx - pad_x, maxx + pad_x)
    ax.set_ylim(miny - pad_y, maxy + pad_y)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    _draw_legend(ax)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("#333333")
        spine.set_linewidth(0.8)
    fig.tight_layout()
    return _fig_to_png(fig)


def _render_points_only(stations: list[dict[str, Any]], title: str) -> bytes:
    import matplotlib.pyplot as plt

    lons = [float(s["lon"]) for s in stations]
    lats = [float(s["lat"]) for s in stations]
    minx, maxx = min(lons), max(lons)
    miny, maxy = min(lats), max(lats)
    span_x = max(maxx - minx, 0.08)
    span_y = max(maxy - miny, 0.08)
    fig, ax = plt.subplots(1, 1, figsize=(11, 9.5))
    ax.set_facecolor("white")
    ax.set_title(title, fontsize=16, pad=16)
    _draw_stations(ax, stations, minx - span_x * 0.1, maxx + span_x * 0.1, miny - span_y * 0.1, maxy + span_y * 0.1)
    ax.set_xlim(minx - span_x * 0.25, maxx + span_x * 0.25)
    ax.set_ylim(miny - span_y * 0.25, maxy + span_y * 0.25)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xticks([])
    ax.set_yticks([])
    _draw_legend(ax)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("#333333")
        spine.set_linewidth(0.8)
    fig.tight_layout()
    return _fig_to_png(fig)


def render_subsidence_map_png(
    rows: list[dict[str, Any]] | None,
    *,
    title: str = "北京市地面沉降/回弹监测分布图",
) -> tuple[bytes | None, str]:
    """返回 (png_bytes, renderer_tag)。失败 (None, reason)。"""
    if not map_render_enabled():
        return None, "disabled"
    if not _ensure_mpl():
        return None, "matplotlib_missing"
    stations = stations_from_layer0_rows(rows)
    if not stations:
        return None, "no_stations"
    assets = default_map_assets_dir()
    try:
        basemap = _load_basemap(assets)
        if basemap is not None:
            return _render_with_basemap(stations, title, basemap), "offline_gis"
        return _render_points_only(stations, title), "offline_points"
    except Exception:
        logger.warning("offline_map render failed", exc_info=True)
        return None, "render_error"


def png_to_base64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")
