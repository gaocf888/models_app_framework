# 地降 NL2SQL QA 种子样例（nl2sql_qa_examples）

> 摄入命名空间：`nl2sql_qa_examples`  
> 方言：PostgreSQL · 表白名单 8 表  
> 灌库时 metadata 须带 `analysis_type`、`plan_item_id`、`plan_template_version=v1`

---

## subsidence_daily · q1 · 当日站点沉降快照

**问句（示例）**：查询昨日各监测站点分层标 total_settle，含行政区。

**SQL（骨架，时间窗由 NL2SQL 改写注入）**：

```sql
SELECT f.station_id, f.station_name, s.area, f.data_time, f.total_settle
FROM t_data_wash_fcb AS f
JOIN t_station AS s ON f.project_name = s.name
WHERE f.data_time >= @t_start AND f.data_time < @t_end
ORDER BY f.station_id, f.data_time DESC
```

---

## subsidence_daily · q2 · 当日行政区汇总

**问句**：昨日按行政区汇总监测站点数与 total_settle 水平。

```sql
SELECT s.area, COUNT(DISTINCT f.station_id) AS station_cnt,
       AVG(f.total_settle) AS avg_total_settle,
       MAX(f.total_settle) AS max_total_settle,
       MIN(f.total_settle) AS min_total_settle
FROM t_data_wash_fcb AS f
JOIN t_station AS s ON f.project_name = s.name
WHERE f.data_time >= @t_start AND f.data_time < @t_end
GROUP BY s.area
ORDER BY s.area
```

---

## subsidence_weekly · q1 · 本周站点周期沉降

**问句**：本周各站点 total_settle 初末差值（周期沉降量 mm）。

```sql
WITH ranked AS (
  SELECT f.station_id, f.station_name, f.total_settle, f.data_time,
         ROW_NUMBER() OVER (PARTITION BY f.station_id ORDER BY f.data_time ASC) AS rn_asc,
         ROW_NUMBER() OVER (PARTITION BY f.station_id ORDER BY f.data_time DESC) AS rn_desc
  FROM t_data_wash_fcb AS f
  WHERE f.data_time >= @t_start AND f.data_time < @t_end
)
SELECT station_id, station_name,
       MAX(CASE WHEN rn_asc = 1 THEN total_settle END) AS settle_start,
       MAX(CASE WHEN rn_desc = 1 THEN total_settle END) AS settle_end,
       MAX(CASE WHEN rn_desc = 1 THEN total_settle END)
         - MAX(CASE WHEN rn_asc = 1 THEN total_settle END) AS period_settle_mm
FROM ranked
GROUP BY station_id, station_name
ORDER BY period_settle_mm
```

---

## subsidence_monthly · q1 · 本月分层标周期沉降

**问句**：本月朝阳区分层标站点周期沉降量。

```sql
WITH ranked AS (
  SELECT f.station_id, f.station_name, s.area, f.total_settle, f.data_time,
         ROW_NUMBER() OVER (PARTITION BY f.station_id ORDER BY f.data_time ASC) AS rn_asc,
         ROW_NUMBER() OVER (PARTITION BY f.station_id ORDER BY f.data_time DESC) AS rn_desc
  FROM t_data_wash_fcb AS f
  JOIN t_station AS s ON f.project_name = s.name
  WHERE f.data_time >= @t_start AND f.data_time < @t_end AND s.area = @district
)
SELECT station_id, station_name, area,
       MAX(CASE WHEN rn_desc = 1 THEN total_settle END)
         - MAX(CASE WHEN rn_asc = 1 THEN total_settle END) AS period_settle_mm
FROM ranked
GROUP BY station_id, station_name, area
ORDER BY period_settle_mm
```

---

## subsidence_quarterly · q1 · 本季度主表沉降

**问句**：本季度通州区各站点季初季末 total_settle 差值。

（SQL 结构同 weekly q1，时间窗为季度；行政区过滤 `s.area = '通州区'`。）

---

## subsidence_quarterly · q2 · 地下水辅助

**问句**：本季度与分层标同 project_name 的地下水 deep/elevation 变化。

```sql
SELECT d.project_name, d.station_name, d.data_time, d.deep, d.elevation
FROM t_data_wash_dxswj AS d
WHERE d.data_time >= @t_start AND d.data_time < @t_end
  AND d.project_name IN (SELECT DISTINCT project_name FROM t_data_wash_fcb
                         WHERE data_time >= @t_start AND data_time < @t_end)
ORDER BY d.project_name, d.data_time
```

---

## subsidence_yearly · q4 · GNSS 年度位移

**问句**：本年度 GNSS 站点 displacement_3d 极值（勿用 fcb.total_settle）。

```sql
SELECT g.station_id, g.station_name,
       MIN(g.displacement_3d) AS min_disp3d,
       MAX(g.displacement_3d) AS max_disp3d
FROM t_data_wash_gnss AS g
WHERE g.data_time >= @t_start AND g.data_time < @t_end
GROUP BY g.station_id, g.station_name
ORDER BY max_disp3d DESC
```

---

## 自然语言直查（无 plan 槽位）

对话查数场景可不带 `plan_item_id`；样例标签 `analysis_type=nl2sql_direct` 或留空，走向量检索 Top-K。

**问句**：朝阳区最近一周分层标沉降最大的 5 个站点。

（由 NL2SQL 全链路生成；灌库前须在目标 PG 库校验可执行。）
