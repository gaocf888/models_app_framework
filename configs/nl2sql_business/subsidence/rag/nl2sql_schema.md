# 地降 NL2SQL Schema 知识（nl2sql_schema）
# RAG 摄入命名空间：nl2sql_schema；方言 PostgreSQL；沉降 8 表 + 运维 om_result 8 表。
# 运行时 catalog 以库反射为准，本文档供召回与人工校对。业务包说明见上级「配置项说明.md」。

> 部署 `NL2SQL_BUSINESS_DOMAIN=subsidence`；方言 PostgreSQL。
> 沉降表在 active 沉降 schema（默认 public）；运维表在 **om_result**，须写 `om_result.t_om_*`。
> 运行时 catalog 以库反射为准，本文档供 RAG 召回与人工校对。

## 遗留主键列（禁止用于关联与过滤）

各 `t_data_wash_*` 事实表中的 `id`、`data_id`、`station_id` 为数据清洗前遗留的联合主键/溯源字段，**不代表**现行业务上的「站点编码」或「监测点身份」。

**禁止：**

- 用 `station_id` / `id` / `data_id` 做 `WHERE` 过滤（如 `station_id = 'F22'`）
- 用上述列与 `t_station` 或其它表做 JOIN
- 把问句中的站点简称（如「尹家河」→ `F22`）写成 `station_id` 条件

**应使用：**

- 场地/站点范围：`project_name`（≡ `t_station.name`，如 `F22(尹家河)`）
- 分层标/基岩标标编号：`station_name`（如 `F8-10`；仅标粒度问句）
- 行政区：`t_station.area`（经 `project_name = t_station.name` JOIN）
- 时间：`data_time`

> 说明：`t_station.id` / `t_station.code` 是维表站点编码，与事实表遗留列 `station_id` **不是**同一业务键；事实表关联维表只用 `project_name = t_station.name`。

## t_station（监测站点维表）

- **用途**：行政区、站点名称、编码、坐标
- **列**：`id`（维表站点编码）、`name`（站点名称，与事实表 `project_name` 关联）、`code`、`lon`、`lat`、`area`（行政区）
- **关联**：各 `t_data_wash_*` 事实表通过 `project_name = t_station.name` JOIN 获取 `area`
- **监测方式覆盖**：官方场地名单见 `device_station_map.yaml`；清单/范围过滤应 `name IN (覆盖名单)` 且可再滤 `area`，勿用全区 `t_station` 代替某监测方式站点集

## t_data_wash_fcb（分层标 · 主沉降表）

- **用途**：地面沉降主分析数据（默认主表）
- **关键列**：`project_name`、`station_name`、`data_time`（观测时间）、`total_settle`（累计沉降 mm，主指标）
- **遗留列（勿过滤/JOIN）**：`id`、`data_id`、`station_id`
- **口径**：周期沉降量 `Δ = total_settle(窗初) − total_settle(窗末)`（初−末）；**Δ>0 下沉倾向，Δ<0 回弹**。站点沉降须用监测层位=0 的 `station_name`（见 `fcb_layer_map.yaml` / `nl2sql_biz_knowledge_fcb_layer0_stations.md`），禁止对同站全部标聚合。

## t_data_wash_jyb（基岩标）

- **用途**：基岩标沉降，辅助对照分层标
- **关键列**：同 fcb——`project_name`、`station_name`、`data_time`、`total_settle`（勿用 `id`/`data_id`/`station_id`）

## t_data_wash_gnss（GNSS 位移）

- **用途**：GNSS 水平/三维位移，**非**分层标 total_settle
- **关键列**：`project_name`、`data_time`、`displacement_2d`、`displacement_3d`（及可选 `station_name` 展示）
- **遗留列（勿过滤/JOIN）**：`id`、`data_id`、`station_id`

## t_data_wash_dxswj（地下水 / 水位井）

- **用途**：地下水埋深、标高
- **关键列**：`project_name`、`data_time`、`deep`（埋深 m）、`elevation`（标高）
- **遗留列（勿过滤/JOIN）**：`id`、`data_id`、`station_id`
- **禁止**：用本表表示分层标沉降 total_settle

## t_data_wash_kxsylj（孔隙水压力）

- **关键列**：`project_name`、`data_time`、`pressure`
- **遗留列（勿过滤/JOIN）**：`id`、`data_id`、`station_id`

## t_data_wash_gq（光纤沉降）

- **关键列**：`project_name`、`data_time`、`total_settle`
- **遗留列（勿过滤/JOIN）**：`id`、`data_id`、`station_id`

## t_data_wash_qxz（气象站）

- **关键列**：`project_name`、`data_time`、`temp`（气温）、`real_time_rain`（降水）
- **遗留列（勿过滤/JOIN）**：`id`、`data_id`、`station_id`

## JOIN 白名单（初始）

- `t_data_wash_fcb.project_name = t_station.name`
- 其他 `t_data_wash_*` 同理通过 `project_name` 关联 `t_station.name`
- 跨事实表 JOIN：同 `project_name` + 时间窗对齐；勿臆造无 FK 的列
- **禁止** `t_data_wash_*.station_id` / `id` / `data_id` 参与任何 JOIN

## 时间列

- 沉降事实表：统一使用 `data_time` 作为过滤与排序时间列
- 运维表：统一使用 `snapshot_time`（批次）；查询必须过滤最新批次

## 运维中间库 schema：om_result（8 表）

> 与沉降 wash 表不同源；禁止与 `t_data_wash_*` 混在同一条 SQL。仪器类型列 `type` = 监测类型中文（地下水位井、光纤、基岩标/分层标、孔隙水压力井、气象站、GNSS）。

通用列：每表均有 `snapshot_time`（同批 8 表该值相同）。最新批过滤示例：

`snapshot_time = (SELECT max(snapshot_time) FROM om_result.t_om_data_alarm_summary)`

### t_om_data_alarm_summary（告警总览，1 行）

- 列：`total_count`、`alarm_count`、`normal_count`、`week_avg`、`month_avg`、`snapshot_time`
- **当前告警占比**：`alarm_count / total_count * 100`；`week_avg`/`month_avg` 为近 7/30 日平均占比，勿混用

### t_om_device_alarm_list（当前有警仪器）

- 列：`id`、`device_id`、`device_name`（标编号）、`data_type`、`source`、`type`、`area_name`、`status`（恒「有警」）、`ss_value`、`yj_value`、`content`、`off_time`、`create_time`、`snapshot_time`
- 告警持续时间：`now() - off_time`（off_time 空则未知）

### t_om_device_online_by_type（按类型在线）

- 列：`type`、`total`、`online`、`offline`、`online_rate`、`snapshot_time`

### t_om_complete_rate_by_type / t_om_effective_rate_by_type

- 列：`type`、`device_count`、`actual_count`、`invalid_count`（仅有效率表）、`theory_count`、`rate`、`window_start`、`window_end`、`snapshot_time`
- 窗口约 T−5~T−2（不含最近两天）

### t_om_offline_device_list

- 列：`device_key`、`name`、`area_name`、`sn`、`type`、`status`、`data_collect_time`、`snapshot_time`
- **必须** `status = 0`（当前离线）；离线时长：`now() - data_collect_time`（天数可用 `FLOOR(EXTRACT(EPOCH FROM …)/86400)`）；勿依赖 `create_time`

### t_om_month_complete_list / t_om_month_effective_list

- **仅含**完整率&lt;92% / 有效率&lt;90% 的设备；达标设备不在表中
- 列含 `name`、`type`、`area_name`、`complete_rate`、`monitor_count`、`theory_count`、`window_*`、`snapshot_time`
