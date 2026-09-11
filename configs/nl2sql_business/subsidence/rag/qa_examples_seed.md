# 地降 NL2SQL QA 种子样例（nl2sql_qa_examples）
# RAG 摄入命名空间：nl2sql_qa_examples；供少样本示范，非单元测试黄金集。
# 周期口径：Δ=初−末；站点用层位0。业务包说明见上级「配置项说明.md」。

> 摄入命名空间：`nl2sql_qa_examples`  
> 方言：PostgreSQL · 表白名单 8 表  
> **周期口径**：`Δ = total_settle(窗初) − total_settle(窗末)`；**Δ>0 下沉**；站点沉降用监测层位=0 的 `station_name`  
> 灌库时 metadata 须带 `analysis_type`、`plan_item_id`、`plan_template_version=v1`

---

## subsidence_daily · q1 · 当日站点沉降快照

**问句（示例）**：查询昨日各监测站点分层标 total_settle，含行政区。

> 站点粒度应优先层位 0 代表标；若取快照可先按 `project_name` 再限定代表 `station_name`（见 `nl2sql_biz_knowledge_fcb_layer0_stations.md`）。

**SQL（骨架，时间窗由 NL2SQL 改写注入）**：

```sql
SELECT f.project_name, f.station_name, s.area, f.data_time, f.total_settle
FROM t_data_wash_fcb AS f
JOIN t_station AS s ON f.project_name = s.name
WHERE f.data_time >= @t_start AND f.data_time < @t_end
ORDER BY f.project_name, f.data_time DESC
```

---

## subsidence_daily · q2 · 当日行政区汇总

**问句**：昨日按行政区汇总监测站点数与 total_settle 水平。

```sql
SELECT s.area, COUNT(DISTINCT f.project_name) AS station_cnt,
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

## subsidence_weekly · q1 · 本周站点周期沉降（层位0 + 初−末）

**问句**：本周各站点 total_settle 周期沉降量（初值减末值，mm）。

> 站点维度：每个 `project_name` 仅用监测层位=0 的标（示例：`F8(周村)`→`F8-10`）。  
> `period_settle_mm = settle_start − settle_end`；**数值越大表示下沉越多**，排序用 `ORDER BY period_settle_mm DESC`。

```sql
WITH ranked AS (
  SELECT f.project_name, f.station_name, f.total_settle, f.data_time,
         ROW_NUMBER() OVER (PARTITION BY f.project_name, f.station_name ORDER BY f.data_time ASC) AS rn_asc,
         ROW_NUMBER() OVER (PARTITION BY f.project_name, f.station_name ORDER BY f.data_time DESC) AS rn_desc
  FROM t_data_wash_fcb AS f
  WHERE f.data_time >= @t_start AND f.data_time < @t_end
    AND f.station_name IN (
      'F8-10','F9-6','F10-7','F11-4','F1-7','F2-7','F27-8','F28-10','F7-7'
      /* 完整清单见 nl2sql_biz_knowledge_fcb_layer0_stations.md；生成时按范围裁剪 */
    )
)
SELECT project_name, station_name,
       MAX(CASE WHEN rn_asc = 1 THEN total_settle END) AS settle_start,
       MAX(CASE WHEN rn_desc = 1 THEN total_settle END) AS settle_end,
       MAX(CASE WHEN rn_asc = 1 THEN total_settle END)
         - MAX(CASE WHEN rn_desc = 1 THEN total_settle END) AS period_settle_mm
FROM ranked
GROUP BY project_name, station_name
ORDER BY period_settle_mm DESC
```

---

## subsidence_monthly · q1 · 本月分层标站点周期沉降

**问句**：本月朝阳区分层标站点周期沉降量。

```sql
WITH ranked AS (
  SELECT f.project_name, f.station_name, s.area, f.total_settle, f.data_time,
         ROW_NUMBER() OVER (PARTITION BY f.project_name, f.station_name ORDER BY f.data_time ASC) AS rn_asc,
         ROW_NUMBER() OVER (PARTITION BY f.project_name, f.station_name ORDER BY f.data_time DESC) AS rn_desc
  FROM t_data_wash_fcb AS f
  JOIN t_station AS s ON f.project_name = s.name
  WHERE f.data_time >= @t_start AND f.data_time < @t_end AND s.area = @district
    AND f.station_name IN ('F1-7','F2-7','F27-8','F28-10','F29-10')
)
SELECT project_name, station_name, area,
       MAX(CASE WHEN rn_asc = 1 THEN total_settle END)
         - MAX(CASE WHEN rn_desc = 1 THEN total_settle END) AS period_settle_mm
FROM ranked
GROUP BY project_name, station_name, area
ORDER BY period_settle_mm DESC
```

---

## subsidence_quarterly · q1 · 本季度主表沉降

**问句**：本季度通州区各站点季初季末 total_settle 差值（初减末）。

（SQL 结构同 weekly q1；行政区过滤 `s.area = '通州区'`；站点用层位 0 标。）

---

## nl2sql_direct · 单站层位0周期沉降

**问句**：F8(周村) 本季度站点沉降量。

```sql
WITH ranked AS (
  SELECT f.total_settle, f.data_time,
         ROW_NUMBER() OVER (ORDER BY f.data_time ASC) AS rn_asc,
         ROW_NUMBER() OVER (ORDER BY f.data_time DESC) AS rn_desc
  FROM t_data_wash_fcb AS f
  WHERE f.project_name = 'F8(周村)' AND f.station_name = 'F8-10'
    AND f.data_time >= @t_start AND f.data_time < @t_end
)
SELECT
  MAX(CASE WHEN rn_asc = 1 THEN total_settle END)
    - MAX(CASE WHEN rn_desc = 1 THEN total_settle END) AS period_settle_mm
FROM ranked
```

---

## nl2sql_direct · 压缩层 0-1 层间差（示意）

**问句**：F8(周村) 本季度浅部第一压缩层（层位0相对层位1）压缩量。

> `compress(0→1) = Δ(F8-10) − Δ(F8-7)`；边界标来自 `fcb_layer_map`。

```sql
-- 分别计算层位0(F8-10)与层位1(F8-7)的 Δ=初−末，再相减；可用两次子查询或条件聚合实现
```

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

**问句**：朝阳区最近一周分层标沉降最大的 5 个站点。

> 「沉降最大」= 周期 `Δ=初−末` **最大**（下沉最多）；按 `project_name` 取层位 0 标后再排序 LIMIT 5。
