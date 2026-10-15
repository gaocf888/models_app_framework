# 离线地图资产（自动报告沉降分布图）

供 `app/analysis_agent/renderers/offline_map.py` 读取。默认路径即本目录，可用 `ANALYSIS_AGENT_MAP_ASSETS_DIR` 覆盖。

```
shapefile/北京市.{shp,shx,dbf,prj}   # 区县界（必填，完整四件套）
dem/beijing.tif                     # 可选 DEM 山体阴影
waterway/                           # 可选 OSM 河网（需完整 shp 四件套才加载）
water_area/
```

水系目录目前仅有 `.shp`、缺 `.dbf/.shx/.prj` 时仍会按市界包围盒裁切绘制（无需 geopandas）。
关闭出图：`ANALYSIS_AGENT_MAP_RENDER_ENABLED=false`。

中文标注依赖系统或本目录字体：把 `NotoSansSC-Regular.otf` / `wqy-zenhei.ttc` 放到 `fonts/` 即可；
Linux 镜像也可 `apt-get install fonts-wqy-zenhei fonts-noto-cjk`。未装中文字体时站名/图例会显示方框。
