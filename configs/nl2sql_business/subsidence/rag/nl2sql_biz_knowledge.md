# 地降 NL2SQL 业务知识（nl2sql_biz_knowledge）
# RAG 摄入命名空间：nl2sql_biz_knowledge；含三维口径与层位规则。
# 业务包说明见上级「配置项说明.md」。

## 监测类型与主辅关系

| 类型 | 表 | 主指标 | 说明 |
|------|-----|--------|------|
| 分层标 | t_data_wash_fcb | total_settle | **沉降主数据**，泛化问沉降默认此表 |
| 基岩标 | t_data_wash_jyb | total_settle | 对照分层标；标编号常为 `J*` |
| GNSS | t_data_wash_gnss | displacement_2d/3d | 位移专题，勿与 fcb 混用 |
| 地下水 | t_data_wash_dxswj | deep, elevation | 水位辅助 |
| 孔隙水 | t_data_wash_kxsylj | pressure | 孔压辅助 |
| 光纤 | t_data_wash_gq | total_settle | 光纤沉降 |
| 气象 | t_data_wash_qxz | temp, real_time_rain | 降水气温辅助 |

## 沉降口径（对齐季报脚本，2026-09 起）

- **周期变化量**：`Δ = total_settle(窗初) − total_settle(窗末)`（mm），即时间窗内**最早有效点减去最晚有效点**（**初 − 末**）。
- **符号**：`Δ > 0` → **下沉倾向**；`Δ < 0` → **回弹/抬升倾向**（与 `季度报告分层标处理.py` 一致）。
- **单点累计**：某时刻 `total_settle` 快照（不是周期差）。
- 问「沉降了多少」未指明监测类型时 → 分层标 `t_data_wash_fcb`。
- 问 GNSS/位移 → `t_data_wash_gnss`，禁止用 fcb.total_settle 代替。
- **口径变更说明**：旧文档曾写「终−初、负值下沉」；现网以本初−末为准。相对旧口径：`Δ_新 = −Δ_旧`。

## 公共表字段说明

- **`project_name`**：站点名称，格式如 `F27(大鲁店)`；与 `t_station.name` 等值关联。
- **`station_name`**：分层标/基岩标**标编号**，格式如 `F27-8`、`J8-1`（库注释若写「站点名称」亦按标编号理解）。
- 禁止把 `station_name` 当成行政区或站点中文名单独过滤行政区。

## 分层标三维计算汇总逻辑

### A. 分层标明细维度

- 对象：某个 `station_name`（标编号）。
- 数据：`t_data_wash_fcb`（`J*` 优先 `t_data_wash_jyb`）该标的 `total_settle` 时间序列。
- 周期沉降：对该标计算 `Δ`（初 − 末）。

### B. 监测站点维度（地面 / 站代表沉降）

- 对象：某个 `project_name`。
- **代表标**：层位字典中该站 **`monitor_layer = 0`** 的 `station_name`（≡ 该站深度最小分层标）。
- 站点周期沉降 = 该代表标上的 `Δ`。
- **禁止**：对同一 `project_name` 下所有 `station_name` 做 AVG/SUM 当作站点沉降。
- 代表标清单见同目录 `nl2sql_biz_knowledge_fcb_layer0_stations.md`；权威映射见 `semantic/dimensions/fcb_layer_map.yaml`。

### C. 压缩层 / 层间压缩维度

- 对象：某站相邻监测层位区间 `i → i+1`（如 `0-1`）。
- 边界标：字典中 `monitor_layer=i` 与 `i+1` 对应的两个 `station_name`（一侧可为基岩标 `J*`）。
- **周期层间压缩量**：`compress(i→i+1) = Δ(层位 i) − Δ(层位 i+1)`。
- **不是**：单独取「标注了监测层位=k 的那根标」的累计 `total_settle` 当作第 k 压缩层沉降。
- `monitor_layer` 为空的中间辅助标：可查该标自身沉降，**不参与**站点代表与层间差。
- 层位不连续（备注「无第四」等）：只算存在的连续对，勿臆造。

### 问句映射

| 用户说法 | 维度 |
|----------|------|
| 某分层标 / `F8-7` / 标编号 | A |
| 某站点沉降 / 地面沉降 / 各站排行 | B（层位 0） |
| 压缩层 / 层间沉降 / 第 1、2 层压缩 | C |
| 「第 N 层分层标」 | 带「压缩层/层位」→ C；形如 `F8-N` → A |

## 行政区与站点范围

- 事实表 **`project_name`** 与 **`t_station.name`** 等值关联。
- 行政区过滤：`t_station.area`（标准名如「朝阳区」「通州区」）；层位字典中的 `district_raw` 可能是简称，**不以之为 SQL 过滤真源**。
- 北京市 16 区：东城、西城、朝阳、丰台、石景山、海淀、门头沟、房山、通州、顺义、昌平、大兴、怀柔、平谷、密云、延庆。

## 层位字典（配置文件）

- 权威文件：`configs/nl2sql_business/subsidence/semantic/dimensions/fcb_layer_map.yaml`（由 `分层标层位分析.xls` 转换入库；层位变更时更新该 YAML）。
- 字段：`project_name`、`station_name`、`borehole_type`、`depth_m`、`monitor_layer`、`note`、`district_raw`。
- **一期不 JOIN 该文件为库表**；生成 SQL 时按本知识与层位 0 清单限定 `station_name`（或写死代表标）。

## 报告分析类型（综合分析）

- `subsidence_daily` / `weekly` / `monthly` / `quarterly` / `yearly`
- 取数计划见 `analysis_plan_subsidence_*`（prompts.yaml）
- QA 槽位五元组：`analysis_type` + `plan_item_id` + `plan_template_version`

## SQL 方言

- 地降库为 **PostgreSQL**：`INTERVAL '7 days'`、`CURRENT_DATE`、`NOW()`
- 禁止 TiDB/MySQL 写法：`INTERVAL 7 DAY`、`DATE_SUB` 等

## 站点规模（快照）

- `t_station` 约 83 个监测站点；层位字典覆盖约 42 个分层标站点、380 条标记录。
- 词表：`scope_lexicon.json`
