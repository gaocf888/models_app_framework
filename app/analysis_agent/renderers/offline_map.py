"""地降自动报告：离线矢量/栅格底图 + 层位 0 站点柱，输出 PNG。

Δ=初−末：Δ<0 沉降（红、柱向下），Δ>0 回弹（绿、柱向上），与 formulas.period_delta / settle_text 一致。
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
_cjk_font_file: str | None = None
_cjk_font_searched = False

_CJK_NAME_HINTS = (
    "Noto Sans CJK",
    "Noto Serif CJK",
    "Noto Sans SC",
    "Noto Serif SC",
    "Source Han Sans",
    "Source Han Serif",
    "WenQuanYi",
    "Droid Sans Fallback",
    "Microsoft YaHei",
    "SimHei",
    "PingFang",
    "Heiti",
)
_CJK_FILE_HINTS = (
    "notosanscjk",
    "notoserifcjk",
    "notosanssc",
    "notoserifsc",
    "sourcehansans",
    "sourcehanserif",
    "wqy-zenhei",
    "wqy-microhei",
    "droid-fallback",
    "msyh",
    "simhei",
    "simsun",
    "pingfang",
    "cjksc",
    "cjk-sc",
    "noto",
)


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
    """有符号周期变化：负=沉降（红），正=回弹（绿）。"""
    if delta_mm < 0:
        return _COLOR_SINK
    if delta_mm > 0:
        return _COLOR_REBOUND
    return _COLOR_ZERO


def stations_from_layer0_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """只保留有 lon/lat/delta 的层位 0 行；val=Δ（负沉降柱向下，正回弹柱向上）。"""
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
                "val": delta,
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


def _repo_map_assets_fonts_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "configs" / "analysis_agent_reports" / "map_assets" / "fonts"


def _candidate_bundled_font_dirs() -> list[Path]:
    """容器内常见挂载点都扫一遍，避免只放在宿主机或 env 指错目录时漏检。"""
    dirs = [
        default_map_assets_dir() / "fonts",
        _repo_map_assets_fonts_dir(),
        Path.cwd() / "configs" / "analysis_agent_reports" / "map_assets" / "fonts",
        Path("/workspace/configs/analysis_agent_reports/map_assets/fonts"),
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        try:
            key = str(d.resolve()) if d.exists() else str(d)
        except Exception:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _iter_font_dirs() -> list[Path]:
    dirs = [
        *_candidate_bundled_font_dirs(),
        Path("/usr/share/fonts"),
        Path("/usr/share/fonts/truetype"),
        Path("/usr/share/fonts/opentype"),
        Path("/usr/share/fonts/opentype/noto"),
        Path("/usr/share/fonts/truetype/noto"),
        Path("/usr/local/share/fonts"),
        Path("C:/Windows/Fonts"),
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        if not d.exists():
            continue
        try:
            key = str(d.resolve())
        except Exception:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out


def _list_font_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    out: list[Path] = []
    for ext in ("*.ttf", "*.otf", "*.ttc", "*.TTF", "*.OTF", "*.TTC"):
        out.extend(p for p in folder.rglob(ext) if p.is_file())
    # 去重（Windows 大小写 glob 可能重复）
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        key = str(p.resolve()).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(p)
    return uniq


def _is_cjk_font_filename(name: str) -> bool:
    n = (name or "").lower()
    if not n.endswith((".ttf", ".otf", ".ttc")):
        return False
    return any(h in n for h in _CJK_FILE_HINTS) or "cjk" in n


def _try_addfont(path: Path) -> None:
    try:
        from matplotlib import font_manager

        font_manager.fontManager.addfont(str(path))
    except Exception:
        logger.info("offline_map addfont skipped path=%s", path)


def resolve_cjk_font_file() -> str | None:
    """优先 map_assets/fonts 下任意 ttf/otf（含 NotoSerifCJKsc），再搜系统字体。"""
    global _cjk_font_file, _cjk_font_searched
    if _cjk_font_searched:
        return _cjk_font_file
    _cjk_font_searched = True
    try:
        from matplotlib import font_manager
    except Exception:
        return None

    bundled_files: list[Path] = []
    for bundled in _candidate_bundled_font_dirs():
        files = _list_font_files(bundled)
        logger.info(
            "offline_map fonts_dir=%s exists=%s files=%s",
            bundled,
            bundled.is_dir(),
            [p.name for p in files],
        )
        bundled_files.extend(files)
    # 用户放入 fonts/ 的文件一律当作中文字体，不再要求文件名含 Sans
    if bundled_files:
        preferred = [p for p in bundled_files if _is_cjk_font_filename(p.name)] or bundled_files
        path = preferred[0]
        _try_addfont(path)
        _cjk_font_file = str(path)
        logger.info("offline_map CJK font=%s", path)
        return _cjk_font_file

    for font in font_manager.fontManager.ttflist:
        blob = f"{font.name or ''} {font.fname or ''}"
        if any(h.lower() in blob.lower() for h in _CJK_NAME_HINTS):
            if font.fname and Path(font.fname).is_file():
                _cjk_font_file = str(font.fname)
                logger.info("offline_map CJK font=%s", _cjk_font_file)
                return _cjk_font_file
    for folder in _iter_font_dirs():
        for path in _list_font_files(folder):
            if not _is_cjk_font_filename(path.name):
                continue
            _try_addfont(path)
            _cjk_font_file = str(path)
            logger.info("offline_map CJK font=%s", path)
            return _cjk_font_file
    logger.warning("offline_map no CJK font found; Chinese labels will tofu")
    return None


def cjk_font_properties(size: float, *, weight: str = "normal"):
    from matplotlib.font_manager import FontProperties

    fname = resolve_cjk_font_file()
    if fname:
        return FontProperties(fname=fname, size=size, weight=weight)
    return FontProperties(size=size, weight=weight)


def _rc_sans_serif_families() -> list[str]:
    """只写入已注册字体名，避免 font_manager 对 SimHei 等缺失族刷 WARNING。"""
    from matplotlib import font_manager

    families: list[str] = []
    cjk = resolve_cjk_font_file()
    if cjk:
        try:
            font_manager.fontManager.addfont(cjk)
        except Exception:
            logger.info("offline_map addfont skipped path=%s", cjk)
        registered: set[str] = set()
        for font in font_manager.fontManager.ttflist:
            if font.fname and os.path.normpath(str(font.fname)) == os.path.normpath(cjk):
                if font.name:
                    registered.add(str(font.name))
        if not registered:
            try:
                name = font_manager.FontProperties(fname=cjk).get_name()
                if name and any(
                    (f.name == name) for f in font_manager.fontManager.ttflist
                ):
                    registered.add(name)
            except Exception:
                pass
        families.extend(sorted(registered))
    families.append("DejaVu Sans")
    return families


def _ensure_mpl() -> bool:
    global _mpl_ready
    if _mpl_ready:
        return True
    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
        import matplotlib.pyplot as plt  # noqa: F401

        plt.rcParams["font.sans-serif"] = _rc_sans_serif_families()
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


def _dem_array_to_hillshade(
    dem: Any,
    *,
    west: float,
    east: float,
    south: float,
    north: float,
    nodata: Any,
) -> tuple[Any, tuple[float, float, float, float]] | None:
    import numpy as np
    from matplotlib.colors import LightSource

    dem = dem.astype(np.float32, copy=False)
    invalid = ~np.isfinite(dem)
    if nodata is not None:
        try:
            nd = float(nodata)
            if np.isfinite(nd):
                invalid |= np.isclose(dem, nd)
        except (TypeError, ValueError):
            pass
    invalid |= (dem < -1000) | (dem > 9000)
    dem[invalid] = np.nan
    if np.all(np.isnan(dem)):
        return None
    extent = (west, east, south, north)
    fill_val = np.nanmedian(dem)
    dem_filled = np.where(np.isnan(dem), fill_val, dem)
    lat_mid = (south + north) / 2.0
    height, width = dem_filled.shape[:2]
    dx = abs((east - west) / max(width, 1)) * 111320.0 * np.cos(np.deg2rad(lat_mid))
    dy = abs((north - south) / max(height, 1)) * 111320.0
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


def _load_dem_hillshade_pil(path: Path) -> tuple[Any, tuple[float, float, float, float]] | None:
    """WGS84 GeoTIFF：无 rasterio 时用 Pillow 读高程。"""
    from PIL import Image
    import numpy as np

    im = Image.open(path)
    tags = getattr(im, "tag_v2", None) or {}
    scale = tags.get(33550)
    tie = tags.get(33922)
    geo = str(tags.get(34737) or "")
    if not scale or not tie:
        return None
    if "WGS" not in geo.upper() and "4326" not in geo:
        logger.info("offline_map PIL DEM skip non-WGS84 crs=%s", geo[:80])
        return None
    sx, sy = float(scale[0]), float(scale[1])
    west, north = float(tie[3]), float(tie[4])
    arr = np.array(im, dtype=np.float32)
    if arr.ndim != 2:
        return None
    height, width = arr.shape
    east = west + width * sx
    south = north - height * sy
    nodata = tags.get(42113)
    return _dem_array_to_hillshade(arr, west=west, east=east, south=south, north=north, nodata=nodata)


def _load_dem_hillshade(path: Path) -> tuple[Any, tuple[float, float, float, float]] | None:
    try:
        import numpy as np
        import rasterio
        from rasterio.warp import Resampling, calculate_default_transform, reproject
    except Exception:
        return _load_dem_hillshade_pil(path)

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
    west, south, east, north = rasterio.transform.array_bounds(dem.shape[0], dem.shape[1], transform)
    return _dem_array_to_hillshade(dem, west=west, east=east, south=south, north=north, nodata=nodata)


def _load_basemap_uncached(assets: Path) -> dict[str, Any] | None:
    shp = assets / "shapefile" / "北京市.shp"
    if not shp.is_file():
        found = list((assets / "shapefile").glob("*.shp")) if (assets / "shapefile").is_dir() else []
        shp = found[0] if found else shp
    if not _shapefile_complete(shp) or not _try_gis_imports():
        lite = _load_basemap_rings(assets)
        if lite is not None:
            return lite
        logger.info("offline_map skip GIS basemap shp_ok=%s gis=%s", _shapefile_complete(shp), _try_gis_imports())
        return None
    import geopandas as gpd
    from shapely.ops import unary_union

    try:
        beijing = gpd.read_file(shp, encoding="gbk").to_crs(epsg=4326)
    except TypeError:
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
        "mode": "gpd",
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


def _rect_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    pad: float = 0.0,
) -> bool:
    return not (a[2] + pad < b[0] or a[0] > b[2] + pad or a[3] + pad < b[1] or a[1] > b[3] + pad)


def _centered_text_box(
    x: float,
    y: float,
    text: str,
    lon_span: float,
    lat_span: float,
    *,
    fontsize: float = 9,
) -> tuple[float, float, float, float]:
    n = max(len(str(text)), 1)
    w = lon_span * 0.012 * (0.55 * n + 1.1) * (fontsize / 9.0)
    h = lat_span * 0.016 * (fontsize / 9.0)
    return (x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0)


def _station_layout_metrics(
    stations: list[dict[str, Any]],
    minx: float,
    maxx: float,
    miny: float,
    maxy: float,
) -> tuple[float, float, float, float, float]:
    lon_span = max(maxx - minx, 1e-6)
    lat_span = max(maxy - miny, 1e-6)
    max_abs = max((abs(float(s["val"])) for s in stations), default=1.0) or 1.0
    height_scale = (lat_span * 0.03) / max_abs
    bar_width = lon_span * 0.007
    fontsize = 7.0 if len(stations) > 24 else 8.0
    return lon_span, lat_span, height_scale, bar_width, fontsize


def _station_bar_rect(
    x: float,
    y: float,
    val: float,
    bar_width: float,
    height_scale: float,
    lat_span: float,
) -> tuple[float, float, float, float]:
    bar_height = float(val) * height_scale
    bar_bottom = y if bar_height >= 0 else y + bar_height
    h = abs(bar_height) or lat_span * 0.002
    half = bar_width / 2.0
    return (x - half, bar_bottom, x + half, bar_bottom + h)


def _station_label_rect(
    x: float,
    y: float,
    *,
    side: str,
    extra: float,
    val_text: str,
    name_text: str,
    bar_width: float,
    lon_span: float,
    lat_span: float,
    fontsize: float,
) -> tuple[float, float, float, float]:
    n = max(len(val_text), len(name_text), 2)
    w = lon_span * 0.011 * (0.55 * n + 1.2) * (fontsize / 8.0)
    h = lat_span * 0.022 * (fontsize / 8.0)
    gap_x = bar_width * 1.4 + extra
    if side == "left":
        x1 = x - gap_x
        return (x1 - w, y - h / 2.0, x1, y + h / 2.0)
    x0 = x + gap_x
    return (x0, y - h / 2.0, x0 + w, y + h / 2.0)


def _prefer_lr_sides(x: float, minx: float, maxx: float, lon_span: float) -> tuple[str, ...]:
    if x > minx + lon_span * 0.72:
        return ("left", "right")
    return ("right", "left")


def _group_lonlat_offsets(lon_span: float, lat_span: float) -> list[tuple[float, float]]:
    """整组（柱+文）经纬度微移：先原位，再左/右/上/下，最后对角。"""
    sx = lon_span * 0.010
    sy = lat_span * 0.010
    out: list[tuple[float, float]] = [(0.0, 0.0)]
    for k in range(1, 6):
        out.extend(
            (
                (-sx * k, 0.0),
                (sx * k, 0.0),
                (0.0, sy * k),
                (0.0, -sy * k),
                (-sx * k, sy * k),
                (sx * k, sy * k),
                (-sx * k, -sy * k),
                (sx * k, -sy * k),
            )
        )
    return out


def _choose_station_group(
    x: float,
    y: float,
    *,
    val: float,
    sides: tuple[str, ...],
    val_text: str,
    name_text: str,
    bar_width: float,
    height_scale: float,
    lon_span: float,
    lat_span: float,
    fontsize: float,
    occupied: list[tuple[float, float, float, float]],
) -> tuple[float, float, str, tuple[float, float, float, float], tuple[float, float, float, float]]:
    """沉降数据/站点名只贴柱子左或右；整组用经纬度微移避开已占用框。"""
    pad = lon_span * 0.0015
    lr = tuple(side for side in sides if side in {"left", "right"}) or ("right", "left")
    fallback = (
        x,
        y,
        lr[0],
        _station_label_rect(
            x, y, side=lr[0], extra=0.0,
            val_text=val_text, name_text=name_text, bar_width=bar_width,
            lon_span=lon_span, lat_span=lat_span, fontsize=fontsize,
        ),
        _station_bar_rect(x, y, val, bar_width, height_scale, lat_span),
    )
    for dx, dy in _group_lonlat_offsets(lon_span, lat_span):
        px, py = x + dx, y + dy
        bar = _station_bar_rect(px, py, val, bar_width, height_scale, lat_span)
        for side in lr:
            label = _station_label_rect(
                px, py, side=side, extra=0.0,
                val_text=val_text, name_text=name_text, bar_width=bar_width,
                lon_span=lon_span, lat_span=lat_span, fontsize=fontsize,
            )
            if not any(
                _rect_overlap(box, other, pad)
                for box in (bar, label)
                for other in occupied
            ):
                return px, py, side, label, bar
            fallback = (px, py, side, label, bar)
    return fallback


def _choose_district_center(
    x: float,
    y: float,
    name: str,
    lon_span: float,
    lat_span: float,
    occupied: list[tuple[float, float, float, float]],
) -> tuple[float, float, tuple[float, float, float, float]]:
    """行政区名以中心点为基准，向左/右/上/下挪开已占用框。"""
    base = _centered_text_box(x, y, name, lon_span, lat_span, fontsize=9)
    w = base[2] - base[0]
    h = base[3] - base[1]
    pad = lon_span * 0.0015
    candidates: list[tuple[float, float]] = [(0.0, 0.0)]
    for k in (1, 2, 3, 4):
        candidates.extend(
            (
                (-w * k, 0.0),
                (w * k, 0.0),
                (0.0, h * k),
                (0.0, -h * k),
            )
        )
    fallback = (x, y, base)
    for dx, dy in candidates:
        cx, cy = x + dx, y + dy
        box = (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)
        if not any(_rect_overlap(box, other, pad) for other in occupied):
            return cx, cy, box
        fallback = (cx, cy, box)
    return fallback


def _district_boxes_from_points(
    points: list[tuple[float, float, str]],
    lon_span: float,
    lat_span: float,
) -> list[tuple[float, float, float, float]]:
    return [item[3] for item in _plan_district_labels(points, lon_span, lat_span)]


def _station_plan_boxes(
    plan: list[tuple[Any, ...]],
) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    for item in plan:
        boxes.append(item[4])
        boxes.append(item[5])
    return boxes


def _plan_station_labels(
    stations: list[dict[str, Any]],
    minx: float,
    maxx: float,
    miny: float,
    maxy: float,
    reserved: list[tuple[float, float, float, float]] | None = None,
) -> list[tuple[dict[str, Any], float, float, str, tuple[float, float, float, float], tuple[float, float, float, float]]]:
    lon_span, lat_span, height_scale, bar_width, fontsize = _station_layout_metrics(
        stations, minx, maxx, miny, maxy
    )
    occupied = list(reserved or [])
    planned: list[
        tuple[dict[str, Any], float, float, str, tuple[float, float, float, float], tuple[float, float, float, float]]
    ] = []
    ordered = sorted(stations, key=lambda s: (float(s["lat"]), float(s["lon"])))
    for s in ordered:
        x, y = float(s["lon"]), float(s["lat"])
        val_text = f"{float(s['delta_mm']):.2f}"
        name_text = str(s["id"])
        sides = _prefer_lr_sides(x, minx, maxx, lon_span)
        px, py, side, label, bar = _choose_station_group(
            x,
            y,
            val=float(s["val"]),
            sides=sides,
            val_text=val_text,
            name_text=name_text,
            bar_width=bar_width,
            height_scale=height_scale,
            lon_span=lon_span,
            lat_span=lat_span,
            fontsize=fontsize,
            occupied=occupied,
        )
        occupied.extend((label, bar))
        planned.append((s, px, py, side, label, bar))
    return planned


def _plan_district_labels(
    points: list[tuple[float, float, str]],
    lon_span: float,
    lat_span: float,
    occupied: list[tuple[float, float, float, float]] | None = None,
) -> list[tuple[float, float, str, tuple[float, float, float, float]]]:
    taken = list(occupied or [])
    placed: list[tuple[float, float, str, tuple[float, float, float, float]]] = []
    for x, y, name in points:
        if not name:
            continue
        cx, cy, box = _choose_district_center(x, y, name, lon_span, lat_span, taken)
        taken.append(box)
        placed.append((cx, cy, name, box))
    return placed


def _draw_district_labels(ax: Any, placed: list[tuple[float, float, str, tuple[float, float, float, float]]]) -> None:
    import matplotlib.patheffects as pe

    text_stroke = [pe.withStroke(linewidth=2.4, foreground="white", alpha=0.75)]
    for cx, cy, name, _box in placed:
        ax.text(
            cx,
            cy,
            name,
            fontsize=9,
            color="#222222",
            ha="center",
            va="center",
            weight="bold",
            zorder=4,
            path_effects=text_stroke,
            fontproperties=cjk_font_properties(9, weight="bold"),
        )


def _draw_stations(
    ax: Any,
    stations: list[dict[str, Any]],
    minx: float,
    maxx: float,
    miny: float,
    maxy: float,
    reserved: list[tuple[float, float, float, float]] | None = None,
    plan: list[tuple[Any, ...]] | None = None,
) -> None:
    import matplotlib.patches as mpatches
    from matplotlib.offsetbox import AnnotationBbox, DrawingArea, TextArea, VPacker
    from matplotlib.textpath import TextPath

    _lon_span, lat_span, height_scale, bar_width, fontsize = _station_layout_metrics(
        stations, minx, maxx, miny, maxy
    )
    if plan is None:
        plan = _plan_station_labels(stations, minx, maxx, miny, maxy, reserved=reserved)

    def _font():
        return cjk_font_properties(fontsize)

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
        tp = dict(size=fontsize, color="#111111", fontproperties=_font())
        line_w = max(_text_width(val_text), _text_width(name_text))
        headed = VPacker(
            children=[TextArea(val_text, textprops=tp), _hline(line_w)],
            align=align,
            pad=0,
            sep=0,
        )
        return VPacker(children=[headed, TextArea(name_text, textprops=tp)], align=align, pad=0, sep=2.0)

    for s, x, y, side, _label, _bar in plan:
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
        val_text = f"{float(s['delta_mm']):.2f}"
        name_text = str(s["id"])
        if side == "left":
            xy, align, box_align = (x - bar_width * 1.4, y), "right", (1.0, 0.5)
        else:
            xy, align, box_align = (x + bar_width * 1.4, y), "left", (0.0, 0.5)
        ab = AnnotationBbox(
            _label_box(val_text, name_text, align),
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

    title = TextArea("图例", textprops=dict(size=11, color="#111111", fontweight="bold", fontproperties=cjk_font_properties(11, weight="bold")))

    def swatch_row(color: str, label: str):
        da = DrawingArea(20, 13, 0, 0)
        da.add_artist(mpatches.Rectangle((1, 2), 16, 9, facecolor=color, edgecolor="#333333", linewidth=0.4))
        return HPacker(children=[da, TextArea(label, textprops=dict(size=10, color="#111111", fontproperties=cjk_font_properties(10)))], align="center", pad=0, sep=6)

    pack = VPacker(
        children=[
            title,
            swatch_row(_COLOR_REBOUND, "地面回弹"),
            swatch_row(_COLOR_SINK, "地面沉降"),
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
    name_col = basemap.get("name_col")
    minx, miny, maxx, maxy = beijing.total_bounds
    lon_span = max(maxx - minx, 1e-6)
    lat_span = max(maxy - miny, 1e-6)
    district_pts: list[tuple[float, float, str]] = []
    if name_col:
        for geom, name in zip(beijing.geometry, beijing[name_col]):
            pt = geom.representative_point()
            district_pts.append((float(pt.x), float(pt.y), str(name)))
    draft = _plan_station_labels(stations, minx, maxx, miny, maxy)
    placed_districts = _plan_district_labels(
        district_pts, lon_span, lat_span, occupied=_station_plan_boxes(draft)
    )
    planned = _plan_station_labels(
        stations,
        minx,
        maxx,
        miny,
        maxy,
        reserved=[item[3] for item in placed_districts],
    )
    _draw_district_labels(ax, placed_districts)
    _draw_stations(ax, stations, minx, maxx, miny, maxy, plan=planned)
    ax.set_title(title, fontsize=16, pad=16, fontproperties=cjk_font_properties(16))
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
    ax.set_title(title, fontsize=16, pad=16, fontproperties=cjk_font_properties(16))
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


def _load_dem_optional(assets: Path) -> tuple[Any, tuple[float, float, float, float] | None] | tuple[None, None]:
    dem_path = assets / "dem" / "beijing.tif"
    if not dem_path.is_file():
        return None, None
    try:
        loaded = _load_dem_hillshade(dem_path)
        if loaded:
            return loaded
    except Exception:
        logger.warning("offline_map DEM hillshade failed", exc_info=True)
        try:
            loaded = _load_dem_hillshade_pil(dem_path)
            if loaded:
                return loaded
        except Exception:
            logger.warning("offline_map PIL DEM failed", exc_info=True)
    return None, None


_WATER_AREA_FCLASS = {"water", "riverbank", "reservoir"}
_MAJOR_RIVER_MIN_KM = 50.0
_MIN_WATER_AREA_M2 = 5.0e4


def _load_lite_major_water(
    assets: Path,
    bounds: tuple[float, float, float, float],
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]]]:
    """lite 水系对齐 demo：大水面 + 有名主干河（同名总长≥50km）。"""
    from app.analysis_agent.renderers.shp_lite import (
        attr_text,
        bbox_intersects,
        first_shp,
        iter_shp_features,
        polyline_length_m,
        ring_area_m2,
        ring_bbox,
    )

    water_polys: list[list[tuple[float, float]]] = []
    water_lines: list[list[tuple[float, float]]] = []
    wa = first_shp(assets / "water_area")
    ww = first_shp(assets / "waterway")
    try:
        if wa is not None:
            for _st, rings, attr in iter_shp_features(wa, want_types=(5,)):
                fclass = attr_text(attr, "fclass", "FCLASS").lower()
                if fclass and fclass not in _WATER_AREA_FCLASS:
                    continue
                ring = rings[0] if rings else []
                bb = ring_bbox(ring)
                if not bb or not bbox_intersects(bb, bounds) or len(ring) < 4:
                    continue
                if ring_area_m2(ring) < _MIN_WATER_AREA_M2:
                    continue
                water_polys.append(ring)
    except Exception:
        logger.warning("offline_map lite water_area skipped", exc_info=True)
        water_polys = []
    try:
        if ww is not None:
            named: dict[str, list[tuple[float, list[tuple[float, float]]]]] = {}
            unnamed_long: list[list[tuple[float, float]]] = []
            has_attr = False
            for _st, rings, attr in iter_shp_features(ww, want_types=(3, 5)):
                if attr:
                    has_attr = True
                fclass = attr_text(attr, "fclass", "FCLASS").lower()
                if fclass and fclass != "river":
                    continue
                name = attr_text(attr, "name", "NAME")
                for ring in rings:
                    bb = ring_bbox(ring)
                    if not bb or not bbox_intersects(bb, bounds) or len(ring) < 2:
                        continue
                    length = polyline_length_m(ring)
                    if name:
                        named.setdefault(name, []).append((length, ring))
                    elif not has_attr and length >= _MAJOR_RIVER_MIN_KM * 1000.0:
                        unnamed_long.append(ring)
            min_m = _MAJOR_RIVER_MIN_KM * 1000.0
            if named:
                for _name, segs in named.items():
                    if sum(item[0] for item in segs) < min_m:
                        continue
                    water_lines.extend(item[1] for item in segs)
            else:
                water_lines.extend(unnamed_long)
    except Exception:
        logger.warning("offline_map lite waterway skipped", exc_info=True)
        water_lines = []
    return water_polys, water_lines


def _load_basemap_rings(assets: Path) -> dict[str, Any] | None:
    """geopandas 不可用时：原生读取区县 shp（WGS84）+ 可选 DEM/水系。"""
    from app.analysis_agent.renderers.shp_lite import (
        first_shp,
        iter_shp_rings,
        read_dbf_rows,
    )

    shp = assets / "shapefile" / "北京市.shp"
    if not shp.is_file():
        alt = first_shp(assets / "shapefile")
        if alt is None:
            return None
        shp = alt
    records = iter_shp_rings(shp, want_types=(5,))
    if not records:
        logger.info("offline_map lite shapefile empty path=%s", shp)
        return None
    dbf = shp.with_suffix(".dbf")
    attrs = read_dbf_rows(dbf) if dbf.is_file() else []
    districts: list[dict[str, Any]] = []
    xs_all: list[float] = []
    ys_all: list[float] = []
    for i, (_stype, rings) in enumerate(records):
        attr = attrs[i] if i < len(attrs) else {}
        name = str(attr.get("district") or attr.get("NAME") or attr.get("name") or attr.get("区县") or "")
        exteriors = [rings[0]] if rings else []
        interiors = rings[1:] if len(rings) > 1 else []
        for ring in rings:
            xs_all.extend(p[0] for p in ring)
            ys_all.extend(p[1] for p in ring)
        districts.append({"name": name, "exteriors": exteriors, "interiors": interiors})
    if not xs_all:
        return None
    bounds = (min(xs_all), min(ys_all), max(xs_all), max(ys_all))
    hillshade, dem_extent = _load_dem_optional(assets)
    water_polys, water_lines = _load_lite_major_water(assets, bounds)
    logger.info(
        "offline_map lite districts=%s water_poly=%s water_line=%s dem=%s",
        len(districts),
        len(water_polys),
        len(water_lines),
        hillshade is not None,
    )
    return {
        "mode": "rings",
        "districts": districts,
        "bounds": bounds,
        "hillshade": hillshade,
        "dem_extent": dem_extent,
        "water_polys": water_polys,
        "water_lines": water_lines,
    }


def _render_with_rings(stations: list[dict[str, Any]], title: str, basemap: dict[str, Any]) -> bytes:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.path import Path as MplPath
    from matplotlib.patches import PathPatch

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
    for ring in basemap.get("water_polys") or []:
        if len(ring) < 3:
            continue
        verts = list(ring)
        codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(verts) - 2) + [MplPath.CLOSEPOLY]
        path = MplPath(verts, codes)
        ax.add_patch(
            PathPatch(path, facecolor="#9dcee8", edgecolor="none", linewidth=0, alpha=0.88, zorder=1)
        )
    for line in basemap.get("water_lines") or []:
        if len(line) < 2:
            continue
        xs, ys = [p[0] for p in line], [p[1] for p in line]
        ax.plot(xs, ys, color="#9dcee8", linewidth=0.9, zorder=1.2, solid_capstyle="round")
    district_pts: list[tuple[float, float, str]] = []
    for dist in basemap.get("districts") or []:
        for ring in dist.get("exteriors") or []:
            if len(ring) < 3:
                continue
            xs, ys = [p[0] for p in ring], [p[1] for p in ring]
            ax.plot(xs, ys, color="#5a5a5a", linewidth=0.8, zorder=2, linestyle=(0, (4.0, 2.4)))
        name = str(dist.get("name") or "")
        ring0 = (dist.get("exteriors") or [None])[0]
        if name and ring0:
            cx = sum(p[0] for p in ring0) / len(ring0)
            cy = sum(p[1] for p in ring0) / len(ring0)
            district_pts.append((cx, cy, name))
    minx, miny, maxx, maxy = basemap["bounds"]
    lon_span = max(maxx - minx, 1e-6)
    lat_span = max(maxy - miny, 1e-6)
    draft = _plan_station_labels(stations, minx, maxx, miny, maxy)
    placed_districts = _plan_district_labels(
        district_pts, lon_span, lat_span, occupied=_station_plan_boxes(draft)
    )
    planned = _plan_station_labels(
        stations,
        minx,
        maxx,
        miny,
        maxy,
        reserved=[item[3] for item in placed_districts],
    )
    _draw_district_labels(ax, placed_districts)
    _draw_stations(ax, stations, minx, maxx, miny, maxy, plan=planned)
    ax.set_title(title, fontsize=16, pad=16, fontproperties=cjk_font_properties(16))
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
            if basemap.get("mode") == "rings":
                return _render_with_rings(stations, title, basemap), "offline_shp"
            return _render_with_basemap(stations, title, basemap), "offline_gis"
        return _render_points_only(stations, title), "offline_points"
    except Exception:
        logger.warning("offline_map render failed", exc_info=True)
        return None, "render_error"


def png_to_base64(png: bytes) -> str:
    return base64.b64encode(png).decode("ascii")
