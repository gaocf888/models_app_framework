import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.patheffects as pe
from matplotlib.colors import LightSource, LinearSegmentedColormap
from matplotlib.font_manager import FontProperties
from matplotlib.offsetbox import (
    AnnotationBbox, DrawingArea, HPacker, TextArea, VPacker,
)
from matplotlib.textpath import TextPath
import numpy as np
import geopandas as gpd
import rasterio
from rasterio.warp import calculate_default_transform, reproject, Resampling
from shapely.ops import unary_union
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch
from pathlib import Path

# ============ 1. 配置全局参数 ============
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'Arial Unicode MS', 'DejaVu Sans', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False

# ============ 2. 加载本地数据 ============
# 相对脚本所在目录，避免从仓库根目录运行时找不到 ./shape&dem_data
_DATA_DIR = Path(__file__).resolve().parent / 'shape&dem_data'
shapefile_path = str(_DATA_DIR / 'shapefile' / '北京市.shp')
dem_path = str(_DATA_DIR / 'dem' / 'beijing.tif')
waterway_path = str(_DATA_DIR / 'waterway' / 'gis_osm_waterways_free_1.shp')
water_area_path = str(_DATA_DIR / 'water_area' / 'gis_osm_water_a_free_1.shp')


def _is_wgs84(crs):
    if crs is None:
        return False
    try:
        return crs.to_epsg() == 4326
    except Exception:
        return str(crs).upper() in ('EPSG:4326', 'WGS84')


def load_dem_hillshade(path):
    """读取 DEM，生成以白底平原为基准的山体阴影。

    不用 LightSource.hillshade() 的全图 min-max 拉伸：那会把平地定在 0.5（中灰）。
    改为相对水平面理论亮度归一化，平地→1（白），只把背光坡面画成阴影。
    """
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
                src_crs, 'EPSG:4326', src.width, src.height, *src.bounds
            )
            dem = np.full((height, width), np.nan, dtype=np.float32)
            reproject(
                source=src_data,
                destination=dem,
                src_transform=src_transform,
                src_crs=src_crs,
                dst_transform=transform,
                dst_crs='EPSG:4326',
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
        raise RuntimeError("DEM 有效高程为空，无法生成山体阴影")

    west, south, east, north = rasterio.transform.array_bounds(
        dem.shape[0], dem.shape[1], transform
    )
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
    flat = float(ls.direction[2])  # 水平面亮度 = sin(海拔角)
    hs = np.clip(intensity / max(flat, 1e-6), 0.0, 1.0)
    # 平原微起伏略低于理论平地，拉到白底，避免再发灰
    hs = np.where(hs >= 0.92, 1.0, hs)
    hs = np.where(np.isnan(dem), np.nan, hs)
    return hs.astype(np.float32), extent


def clip_to_polygon(gdf, clip_geom):
    """将水系裁到市界内，避免画出北京市范围。"""
    if gdf is None or gdf.empty:
        return gdf
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    hits = gdf[gdf.intersects(clip_geom)]
    if hits.empty:
        return hits
    hits = hits.copy()
    hits['geometry'] = hits.geometry.intersection(clip_geom)
    return hits[~hits.geometry.is_empty]


def filter_major_named_rivers(gdf, min_total_km=50.0):
    """只保留有名称、且同名河段总长度达到阈值的主干河。"""
    if gdf is None or gdf.empty:
        return gdf
    named = gdf[gdf['name'].fillna('').str.strip() != ''].copy()
    if named.empty:
        return named
    named['len_m'] = named.to_crs(epsg=3857).geometry.length
    keep_names = (
        named.groupby('name')['len_m'].sum()
        .loc[lambda s: s >= min_total_km * 1000.0]
        .index
    )
    return named[named['name'].isin(keep_names)].drop(columns=['len_m'])


def iter_polygons(geom):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == 'Polygon':
        yield geom
    elif geom.geom_type == 'MultiPolygon':
        yield from geom.geoms


def iter_lines(geom):
    if geom is None or geom.is_empty:
        return
    if geom.geom_type == 'LineString':
        yield geom
    elif geom.geom_type == 'MultiLineString':
        yield from geom.geoms
    elif geom.geom_type == 'GeometryCollection':
        for part in geom.geoms:
            yield from iter_lines(part)


def polygon_to_path(geom):
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


def plot_water_areas(ax, gdf, facecolor, edgecolor, zorder):
    for geom in gdf.geometry:
        for poly in iter_polygons(geom):
            path = polygon_to_path(poly)
            if path is None:
                continue
            ax.add_patch(PathPatch(
                path, facecolor=facecolor, edgecolor='none',
                linewidth=0, alpha=0.88, zorder=zorder, transform=ax.transData,
            ))


def plot_waterways(ax, gdf, zorder):
    width_by_class = {'river': 1.15, 'canal': 0.85, 'stream': 0.45}
    color_by_class = {'river': '#9dcee8', 'canal': '#9dcee8', 'stream': '#9dcee8'}
    for _, row in gdf.iterrows():
        fclass = str(row.get('fclass', 'stream'))
        color = color_by_class.get(fclass, '#9dcee8')
        lw = width_by_class.get(fclass, 0.5)
        for line in iter_lines(row.geometry):
            xs, ys = line.xy
            ax.plot(xs, ys, color=color, linewidth=lw, solid_capstyle='round',
                    solid_joinstyle='round', zorder=zorder, alpha=0.95)


def plot_boundary(ax, geom, color, linewidth, zorder, linestyle='-'):
    """用 matplotlib 绘制多边形边界。"""
    if geom.geom_type == 'Polygon':
        polys = [geom]
    elif geom.geom_type == 'MultiPolygon':
        polys = list(geom.geoms)
    else:
        return
    for poly in polys:
        xs, ys = poly.exterior.xy
        ax.plot(
            xs, ys, color=color, linewidth=linewidth, zorder=zorder,
            linestyle=linestyle, solid_capstyle='round',
        )
        for hole in poly.interiors:
            hxs, hys = hole.xy
            ax.plot(
                hxs, hys, color=color, linewidth=max(linewidth - 0.3, 0.4),
                zorder=zorder, linestyle=linestyle,
            )


def draw_scale_bar(ax, x, y, length_km=15, n_segments=3, zorder=6):
    """绘制带浅色底框的黑白分段比例尺，长度按当地纬度换算为真实公里。"""
    km_per_deg_lon = 111.32 * np.cos(np.deg2rad(y))
    km_per_deg_lat = 111.32
    total_deg = length_km / km_per_deg_lon
    seg_deg = total_deg / n_segments
    seg_km = length_km / n_segments
    bar_h = 1.3 / km_per_deg_lat  # 约 1.3 km 的视觉高度，细长专业

    box_pad_x = total_deg * 0.10
    box_pad_bottom = bar_h * 2.4
    box_pad_top = bar_h * 3.2
    ax.add_patch(mpatches.Rectangle(
        (x - box_pad_x, y - box_pad_bottom),
        total_deg + box_pad_x * 2.8,
        bar_h + box_pad_bottom + box_pad_top,
        facecolor='white',
        edgecolor='#777777',
        linewidth=0.7,
        alpha=0.92,
        zorder=zorder,
    ))
    ax.text(
        x + total_deg / 2.0, y + bar_h * 2.15, '比例尺',
        ha='center', va='bottom', fontsize=8, color='#111111', zorder=zorder + 0.2,
    )

    for i in range(n_segments):
        ax.add_patch(mpatches.Rectangle(
            (x + i * seg_deg, y), seg_deg, bar_h,
            facecolor='#111111' if i % 2 == 0 else 'white',
            edgecolor='#111111', linewidth=0.7, zorder=zorder + 0.1,
        ))
        tick_x = x + i * seg_deg
        ax.plot(
            [tick_x, tick_x], [y, y + bar_h * 1.55],
            color='#111111', linewidth=0.7, zorder=zorder + 0.2,
        )
        ax.text(
            tick_x, y - bar_h * 0.7, f'{int(i * seg_km)}',
            ha='center', va='top', fontsize=8, color='#111111', zorder=zorder + 0.2,
        )

    ax.plot(
        [x + total_deg, x + total_deg], [y, y + bar_h * 1.55],
        color='#111111', linewidth=0.7, zorder=zorder + 0.2,
    )
    ax.text(
        x + total_deg, y - bar_h * 0.7, f'{int(length_km)}',
        ha='center', va='top', fontsize=8, color='#111111', zorder=zorder + 0.2,
    )
    ax.text(
        x + total_deg + box_pad_x * 0.35, y + bar_h * 0.5, 'km',
        ha='left', va='center', fontsize=8, color='#111111', zorder=zorder + 0.2,
    )


def draw_north_arrow(ax, x, y, size=0.025, zorder=6):
    """绘制黑白对半的四角星罗盘指北针。"""
    badge_r = size * 1.88
    fs_n = 12
    fs_c = 9
    ax.add_patch(mpatches.Circle(
        (x, y), badge_r,
        facecolor='white', edgecolor='#555555', linewidth=0.7,
        alpha=0.94, zorder=zorder,
    ))
    ax.add_patch(mpatches.Circle(
        (x, y), size * 1.32,
        facecolor='none', edgecolor='#333333', linewidth=0.4, zorder=zorder + 0.05,
    ))

    def _poly(points, facecolor, z):
        ax.add_patch(mpatches.Polygon(
            points, closed=True,
            facecolor=facecolor, edgecolor='#111111', linewidth=0.3, zorder=z,
        ))

    w = size * 0.30
    ns = size * 1.08
    ew = size * 0.90
    _poly([(x, y + ns), (x, y), (x - w, y)], '#1a1a1a', zorder + 0.25)
    _poly([(x, y + ns), (x, y), (x + w, y)], 'white', zorder + 0.25)
    _poly([(x, y - ns), (x, y), (x - w, y)], 'white', zorder + 0.20)
    _poly([(x, y - ns), (x, y), (x + w, y)], '#1a1a1a', zorder + 0.20)
    _poly([(x + ew, y), (x, y), (x, y + w)], '#1a1a1a', zorder + 0.22)
    _poly([(x + ew, y), (x, y), (x, y - w)], 'white', zorder + 0.22)
    _poly([(x - ew, y), (x, y), (x, y + w)], 'white', zorder + 0.22)
    _poly([(x - ew, y), (x, y), (x, y - w)], '#1a1a1a', zorder + 0.22)

    ax.add_patch(mpatches.Circle(
        (x, y), size * 0.10,
        facecolor='white', edgecolor='#111111', linewidth=0.45, zorder=zorder + 0.35,
    ))
    ax.text(
        x, y + size * 1.52, 'N',
        ha='center', va='center', fontsize=fs_n, fontweight='bold',
        color='#111111', zorder=zorder + 0.4,
    )
    ax.text(x, y - size * 1.48, 'S', ha='center', va='center', fontsize=fs_c, color='#333333', zorder=zorder + 0.4)
    ax.text(x + size * 1.50, y, 'E', ha='center', va='center', fontsize=fs_c, color='#333333', zorder=zorder + 0.4)
    ax.text(x - size * 1.50, y, 'W', ha='center', va='center', fontsize=fs_c, color='#333333', zorder=zorder + 0.4)


def _label_font(fontsize):
    return FontProperties(family='SimHei', size=fontsize)


def _approx_text_width_pt(text, fontsize):
    """按实际字形路径估算文字宽度，避免横线长于标注。"""
    text = str(text)
    try:
        return float(TextPath((0, 0), text, size=fontsize, prop=_label_font(fontsize)).get_extents().width)
    except Exception:
        width = 0.0
        for ch in text:
            width += fontsize * (0.95 if ord(ch) > 127 else 0.50)
        return max(width, fontsize)


def _hline_area(width_pt, thickness=0.72, color='#222222'):
    """横线至少约 2 个输出像素，避免部分站点因像素对齐画丢。"""
    width_pt = max(float(width_pt), 0.8)
    da = DrawingArea(width_pt, thickness, 0, 0)
    da.add_artist(mpatches.Rectangle(
        (0, 0), width_pt, thickness,
        facecolor=color, edgecolor='none', linewidth=0,
        antialiased=False, clip_on=False,
    ))
    return da


def _value_name_stack(val_text, name_text, fontsize, align='left'):
    """数值贴着横线，横线与站名之间留一点空隙。"""
    tp = dict(size=fontsize, color='#111111')
    val_box = TextArea(val_text, textprops=tp)
    name_box = TextArea(name_text, textprops=tp)
    line_w = max(
        _approx_text_width_pt(val_text, fontsize),
        _approx_text_width_pt(name_text, fontsize),
    )
    headed = VPacker(
        children=[val_box, _hline_area(line_w)],
        align=align, pad=0, sep=0,
    )
    return VPacker(
        children=[headed, name_box],
        align=align, pad=0, sep=2.0,
    )


def make_station_label_box(val_text, name_text, fontsize=8, align='left'):
    """数值在上、站名在下、中间横线间隔。"""
    return _value_name_stack(val_text, name_text, fontsize, align=align)


def draw_map_legend(ax):
    """颜色图例 + 站点标注说明（数值 / 站名）。"""
    title = TextArea('图例', textprops=dict(size=12, color='#111111', fontweight='bold'))

    def swatch_row(color, label):
        da = DrawingArea(20, 13, 0, 0)
        da.add_artist(mpatches.Rectangle(
            (1, 2), 16, 9, facecolor=color, edgecolor='#333333', linewidth=0.4,
        ))
        txt = TextArea(label, textprops=dict(size=11, color='#111111'))
        return HPacker(children=[da, txt], align='center', pad=0, sep=6)

    example = HPacker(
        children=[
            _value_name_stack('4.00', '望京', fontsize=10, align='left'),
            _value_name_stack('沉降/上升 (mm)', '地面沉降监测站', fontsize=10, align='left'),
        ],
        align='center', pad=0, sep=10,
    )

    pack = VPacker(
        children=[
            title,
            swatch_row('#2ecc71', '地面回弹'),
            swatch_row('#e74c3c', '地面沉降'),
            swatch_row('#9dcee8', '河流/水体'),
            example,
        ],
        align='left', pad=3, sep=5,
    )
    # 等比例轴下用相同的数据边距，使图例距底边、右边的视觉距离一致
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    margin = 0.07 * (y1 - y0)
    box = AnnotationBbox(
        pack, xy=(x1 - margin, y0 + margin), xycoords='data',
        box_alignment=(1.0, 0.0), frameon=True, pad=0.55,
        zorder=7, annotation_clip=False,
    )
    box.patch.set_facecolor('white')
    box.patch.set_edgecolor('#888888')
    box.patch.set_linewidth(0.8)
    box.patch.set_alpha(0.95)
    ax.add_artist(box)


# 1. 加载矢量地图数据
print("正在加载矢量地图...")
beijing = gpd.read_file(shapefile_path)
beijing = beijing.to_crs(epsg=4326)
city_union = unary_union(beijing.geometry)

# 2. 加载并渲染 DEM 数据
print("正在处理 DEM 地形数据...")
hillshade = None
dem_extent = None
try:
    hillshade, dem_extent = load_dem_hillshade(dem_path)
except Exception as e:
    print(f"[WARN] 无法加载或渲染 DEM: {e}")
else:
    print("[OK] DEM 地形渲染成功")

# 3. 加载 OSM 水系（湖库面 + 河网线），裁到市界内
print("正在加载水系数据...")
water_areas = None
waterways = None
try:
    water_areas = gpd.read_file(water_area_path).to_crs(epsg=4326)
    water_areas = water_areas[water_areas['fclass'].isin(['water', 'riverbank', 'reservoir'])]
    area_m2 = water_areas.to_crs(epsg=3857).geometry.area
    water_areas = water_areas[area_m2 >= 5e4]
    water_areas = clip_to_polygon(water_areas, city_union)
    print(f"[OK] 水体面 {0 if water_areas is None else len(water_areas)} 个")
except Exception as e:
    print(f"[WARN] 无法加载水体面: {e}")
    water_areas = None
try:
    waterways = gpd.read_file(waterway_path).to_crs(epsg=4326)
    # OSM 分级：river > canal > stream > drain；先留最高级，再按同名总长筛主干河
    waterways = waterways[waterways['fclass'] == 'river']
    waterways = clip_to_polygon(waterways, city_union)
    waterways = filter_major_named_rivers(waterways, min_total_km=50.0)
    n_rivers = 0 if waterways is None or waterways.empty else waterways['name'].nunique()
    n_segs = 0 if waterways is None else len(waterways)
    print(f"[OK] 主干河 {n_rivers} 条（河段 {n_segs}）")
except Exception as e:
    print(f"[WARN] 无法加载河网: {e}")
    waterways = None

# ============ 3. 沉降监测站点数据 ============
stations = [
    {"id": "周村", "lon": 117.0626047, "lat": 40.1356135, "val": -8.04},
    {"id": "闫家营", "lon": 116.5229777, "lat": 40.15791657, "val": 0.85},
    {"id": "天竺", "lon": 116.542, "lat": 40.098, "val": 1.56},
    {"id": "牌楼站", "lon": 116.781775, "lat": 40.112223, "val": 2.56},
    {"id": "沟渠庄", "lon": 116.6993758, "lat": 39.95140318, "val": -0.76},
    {"id": "望京", "lon": 116.4713246, "lat": 39.99562302, "val": -2.36},
    {"id": "大鲁店", "lon": 116.5877169, "lat": 39.83964865, "val": 1.56},
    {"id": "东窑", "lon": 116.6066384, "lat": 40.00156618, "val": 1.56},
    {"id": "王四营", "lon": 116.525, "lat": 39.863, "val": 1.56},
    {"id": "金盏", "lon": 116.5690317, "lat": 39.98219174, "val": -8.77},
    {"id": "沙河", "lon": 116.2955557, "lat": 40.12601757, "val": 6.02},
    {"id": "梨花", "lon": 116.3950379, "lat": 39.61623863, "val": -3.15},
    {"id": "孙家营", "lon": 116.4825658, "lat": 39.55664613, "val": -1.89},
    {"id": "求贤", "lon": 116.2730987, "lat": 39.50271277, "val": -2.29},
    {"id": "张家务", "lon": 116.3514295, "lat": 39.49890319, "val": -1.29}
]

# ============ 4. 绘图逻辑 ============
fig, ax = plt.subplots(1, 1, figsize=(14, 12))
ax.set_facecolor('white')

# 1. 绘制灰度山体阴影
if hillshade is not None and dem_extent is not None:
    relief_cmap = LinearSegmentedColormap.from_list(
        'relief_white', ['#8a8a8a', '#ffffff']
    )
    # 无数据区与平原同为白底
    hs_plot = np.nan_to_num(hillshade, nan=1.0)
    ax.imshow(
        hs_plot,
        extent=dem_extent,
        cmap=relief_cmap,
        vmin=0.32,
        vmax=1.0,
        interpolation='bilinear',
        origin='upper',
        zorder=0,
        aspect='auto',
    )

# 2. 绘制水系（叠在 DEM 之上、区县界之下）
if water_areas is not None and not water_areas.empty:
    plot_water_areas(ax, water_areas, facecolor='#9dcee8', edgecolor='none', zorder=1)
if waterways is not None and not waterways.empty:
    plot_waterways(ax, waterways, zorder=1.2)

# 3. 绘制区县边界与名称
for geom in beijing.geometry:
    plot_boundary(
        ax, geom, color='#5a5a5a', linewidth=0.8, zorder=2,
        linestyle=(0, (4.0, 2.4)),
    )
plot_boundary(ax, city_union, color='#c026d3', linewidth=1.8, zorder=3)

text_stroke = [pe.withStroke(linewidth=2.4, foreground='white', alpha=0.75)]
for geom, name in zip(beijing.geometry, beijing['district']):
    pt = geom.representative_point()
    ax.text(
        pt.x, pt.y, name, fontsize=10, color='#222222',
        ha='center', va='center', weight='bold', zorder=4,
        path_effects=text_stroke,
    )

# 3. 绘制沉降/回弹柱状图
minx, miny, maxx, maxy = beijing.total_bounds
lon_span = maxx - minx
lat_span = maxy - miny
max_abs_val = max(abs(s['val']) for s in stations) or 1.0
# 最高柱子约为纬度跨度的 3%，避免东部/南部站点画出市界
height_scale = (lat_span * 0.03) / max_abs_val
bar_width = lon_span * 0.007

for s in stations:
    x, y = s['lon'], s['lat']
    val = s['val']
    color = '#2ecc71' if val > 0 else '#e74c3c'
    bar_height = val * height_scale
    bar_bottom = y if val >= 0 else y + bar_height
    ax.add_patch(mpatches.Rectangle(
        (x - bar_width / 2.0, bar_bottom),
        bar_width,
        abs(bar_height),
        facecolor=color,
        edgecolor='#222222',
        linewidth=0.4,
        zorder=5,
        clip_on=True,
    ))
    # 靠东侧的站点把文字放左边，避免贴出图框；数值在上、站名在下、中间横线
    if x > minx + lon_span * 0.72:
        xy, align, box_align = (x - bar_width * 1.4, y), 'right', (1.0, 0.5)
    else:
        xy, align, box_align = (x + bar_width * 1.4, y), 'left', (0.0, 0.5)
    ab = AnnotationBbox(
        make_station_label_box(f'{val:.2f}', s['id'], fontsize=8, align=align),
        xy, xycoords='data', box_alignment=box_align,
        frameon=False, pad=0.05, zorder=5.5, annotation_clip=True,
    )
    ax.add_artist(ab)

# ============ 5. 绘制比例尺、指北针与图例 ============
ax.set_title("北京市地面沉降/回弹监测分布图", fontsize=18, pad=20)

pad_x = lon_span * 0.035
pad_y = lat_span * 0.035
ax.set_xlim(minx - pad_x, maxx + pad_x)
ax.set_ylim(miny - pad_y, maxy + pad_y)
ax.set_aspect('equal')

# 比例尺放在西南角图面内（房山附近空白处），避免落在图外白边
scale_x = minx + lon_span * 0.03
scale_y = miny + lat_span * 0.045
draw_scale_bar(ax, scale_x, scale_y, length_km=15, n_segments=3)

# 罗盘约为 15 km 色块条长度的 62%，比原先更大，并留在图面东北角灰底内
km_per_deg_lon = 111.32 * np.cos(np.deg2rad(scale_y))
scale_len_deg = 15.0 / km_per_deg_lon
north_size = scale_len_deg * 0.62 / (1.88 * 2.0)
n_x = maxx - lon_span * 0.080
n_y = maxy - lat_span * 0.095
draw_north_arrow(ax, n_x, n_y, size=north_size)

draw_map_legend(ax)

ax.set_xticks([])
ax.set_yticks([])
for spine in ax.spines.values():
    spine.set_visible(True)
    spine.set_color('#333333')
    spine.set_linewidth(0.8)

plt.tight_layout()
_output_png = Path(__file__).resolve().parent / 'beijing_final_map.png'
plt.savefig(_output_png, dpi=200, bbox_inches='tight', facecolor='white')
print(f"[OK] 地图已生成并保存为 {_output_png}")
plt.show()
