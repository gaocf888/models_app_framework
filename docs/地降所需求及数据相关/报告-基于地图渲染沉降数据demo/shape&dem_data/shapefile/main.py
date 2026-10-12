import geopandas as gpd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['WenQuanYi Zen Hei']
plt.rcParams['axes.unicode_minus'] = False

# 读取 Shapefile
gdf = gpd.read_file('北京市.shp')

# 打印基本信息
print("=== 北京市地理信息数据 ===")
print(f"要素数量：{len(gdf)}")
print(f"坐标系：{gdf.crs}")
print(f"\n列名：{list(gdf.columns)}")
print(f"\n前 5 条记录：")
print(gdf.head())

# 创建可视化
fig, ax = plt.subplots(figsize=(12, 10))
gdf.plot(ax=ax, color='lightblue', edgecolor='black', linewidth=0.8)
ax.set_title('北京市行政区划图', fontsize=16)
ax.set_xlabel('经度')
ax.set_ylabel('纬度')
ax.grid(True, alpha=0.3)

# 保存图片
plt.savefig('beijing_map.png', dpi=150, bbox_inches='tight')
print("\n地图已保存为 beijing_map.png")

# 显示统计信息
if len(gdf) > 0:
    print(f"\n几何类型：{gdf.geometry.type.value_counts()}")
    bounds = gdf.total_bounds
    print(f"\n边界范围:")
    print(f"  最小经度：{bounds[0]:.4f}")
    print(f"  最小纬度：{bounds[1]:.4f}")
    print(f"  最大经度：{bounds[2]:.4f}")
    print(f"  最大纬度：{bounds[3]:.4f}")