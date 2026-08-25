# 地降 NL2SQL Schema 知识（nl2sql_schema）

> 部署 `NL2SQL_BUSINESS_DOMAIN=subsidence`；方言 PostgreSQL；表白名单 8 表。
> 运行时 catalog 以库反射为准，本文档供 RAG 召回与人工校对。

## t_station（监测站点维表）

- **用途**：行政区、站点名称、编码、坐标
- **列**：`id`（站点编码）、`name`（站点名称，与事实表 `project_name` 关联）、`code`、`lon`、`lat`、`area`（行政区）
- **关联**：各 `t_data_wash_*` 事实表通过 `project_name = t_station.name` JOIN 获取 `area`

## t_data_wash_fcb（分层标 · 主沉降表）

- **用途**：地面沉降主分析数据（默认主表）
- **关键列**：`station_id`、`station_name`、`project_name`、`data_time`（观测时间）、`total_settle`（累计沉降 mm，主指标）
- **口径**：周期沉降量 = 时间窗内 `total_settle` 终值减初值；负值表示下沉

## t_data_wash_jyb（基岩标）

- **用途**：基岩标沉降，辅助对照分层标
- **关键列**：同 fcb 结构，主指标 `total_settle`，时间 `data_time`

## t_data_wash_gnss（GNSS 位移）

- **用途**：GNSS 水平/三维位移，**非**分层标 total_settle
- **关键列**：`displacement_2d`、`displacement_3d`、`data_time`、`station_id`、`station_name`、`project_name`

## t_data_wash_dxswj（地下水 / 水位井）

- **用途**：地下水埋深、标高
- **关键列**：`deep`（埋深 m）、`elevation`（标高）、`data_time`、`project_name`
- **禁止**：用本表表示分层标沉降 total_settle

## t_data_wash_kxsylj（孔隙水压力）

- **关键列**：`pressure`、`data_time`、`project_name`

## t_data_wash_gq（光纤沉降）

- **关键列**：`total_settle`、`data_time`、`project_name`

## t_data_wash_qxz（气象站）

- **关键列**：`temp`（气温）、`real_time_rain`（降水）、`data_time`、`project_name`

## JOIN 白名单（初始）

- `t_data_wash_fcb.project_name = t_station.name`
- 其他 `t_data_wash_*` 同理通过 `project_name` 关联 `t_station.name`
- 跨事实表 JOIN：同 `project_name` + 时间窗对齐；勿臆造无 FK 的列

## 时间列

- 统一使用各事实表的 `data_time` 作为过滤与排序时间列
