# 地降 NL2SQL 业务知识（nl2sql_biz_knowledge）

## 监测类型与主辅关系

| 类型 | 表 | 主指标 | 说明 |
|------|-----|--------|------|
| 分层标 | t_data_wash_fcb | total_settle | **沉降主数据**，泛化问沉降默认此表 |
| 基岩标 | t_data_wash_jyb | total_settle | 对照分层标 |
| GNSS | t_data_wash_gnss | displacement_2d/3d | 位移专题，勿与 fcb 混用 |
| 地下水 | t_data_wash_dxswj | deep, elevation | 水位辅助 |
| 孔隙水 | t_data_wash_kxsylj | pressure | 孔压辅助 |
| 光纤 | t_data_wash_gq | total_settle | 光纤沉降 |
| 气象 | t_data_wash_qxz | temp, real_time_rain | 降水气温辅助 |

## 沉降口径

- **周期沉降/回弹**：窗口起止 `data_time` 对应 `total_settle` 的差值（mm）
- **单点累计**：某时刻 `total_settle` 快照值
- 问「沉降了多少」未指明监测类型时 → 分层标 `t_data_wash_fcb`
- 问 GNSS/位移 → `t_data_wash_gnss`，禁止用 fcb.total_settle 代替

## 行政区与站点范围

- 事实表 **`project_name`** 与 **`t_station.name`** 等值关联
- 行政区过滤：`t_station.area`（如「朝阳区」「通州区」）
- 站点过滤：`station_id`、`station_name`，或规则解析出的站点词条
- 北京市 16 区：东城、西城、朝阳、丰台、石景山、海淀、门头沟、房山、通州、顺义、昌平、大兴、怀柔、平谷、密云、延庆

## 报告分析类型（综合分析）

- `subsidence_daily` / `weekly` / `monthly` / `quarterly` / `yearly`
- 取数计划见 `analysis_plan_subsidence_*`（prompts.yaml v1）
- QA 槽位五元组：`analysis_type` + `plan_item_id`（q1…）+ `plan_template_version=v1`

## SQL 方言

- 地降库为 **PostgreSQL**：`INTERVAL '7 days'`、`CURRENT_DATE`、`NOW()`
- 禁止 TiDB/MySQL 写法：`INTERVAL 7 DAY`、`DATE_SUB` 等

## 站点规模（快照）

- 当前 `t_station` 约 83 个监测站点，覆盖上述 16 区
- 词表文件：`scope_lexicon.json`（由 `t_station_snapshot.tsv` 或库导出维护）
