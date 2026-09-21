-- 分层标/基岩标窗初窗末累计沉降（半开 [t_start, t_end)）
-- 绑定：t_start, t_end, fcb_marks, jyb_marks, fcb_projects, jyb_projects, area
-- 仅 station_name ∈ 层位字典（有 monitor_layer）且 project_name ∈ 对应覆盖名单
-- 初末 data_time 相同或缺一侧 → 不输出（等同脚本 <2 个有效点）
-- 禁止用事实表「窗内出现过的场地」代替 allowlist

WITH fcb_raw AS (
  SELECT
    w.project_name,
    w.station_name,
    w.total_settle,
    w.data_time,
    ROW_NUMBER() OVER (
      PARTITION BY w.project_name, w.station_name ORDER BY w.data_time ASC
    ) AS rn_asc,
    ROW_NUMBER() OVER (
      PARTITION BY w.project_name, w.station_name ORDER BY w.data_time DESC
    ) AS rn_desc
  FROM t_data_wash_fcb w
  WHERE cardinality(CAST(:fcb_marks AS text[])) > 0
    AND w.station_name = ANY(CAST(:fcb_marks AS text[]))
    AND w.project_name = ANY(CAST(:fcb_projects AS text[]))
    AND w.data_time >= CAST(:t_start AS timestamp)
    AND w.data_time < CAST(:t_end AS timestamp)
    AND w.total_settle IS NOT NULL
),
jyb_raw AS (
  SELECT
    w.project_name,
    w.station_name,
    w.total_settle,
    w.data_time,
    ROW_NUMBER() OVER (
      PARTITION BY w.project_name, w.station_name ORDER BY w.data_time ASC
    ) AS rn_asc,
    ROW_NUMBER() OVER (
      PARTITION BY w.project_name, w.station_name ORDER BY w.data_time DESC
    ) AS rn_desc
  FROM t_data_wash_jyb w
  WHERE cardinality(CAST(:jyb_marks AS text[])) > 0
    AND w.station_name = ANY(CAST(:jyb_marks AS text[]))
    AND w.project_name = ANY(CAST(:jyb_projects AS text[]))
    AND w.data_time >= CAST(:t_start AS timestamp)
    AND w.data_time < CAST(:t_end AS timestamp)
    AND w.total_settle IS NOT NULL
),
union_raw AS (
  SELECT * FROM fcb_raw
  UNION ALL
  SELECT * FROM jyb_raw
),
paired AS (
  SELECT
    a.project_name,
    a.station_name,
    a.total_settle AS settle_start,
    a.data_time AS time_start,
    b.total_settle AS settle_end,
    b.data_time AS time_end
  FROM union_raw a
  JOIN union_raw b
    ON a.project_name = b.project_name
   AND a.station_name = b.station_name
   AND a.rn_asc = 1
   AND b.rn_desc = 1
  WHERE a.data_time <> b.data_time
)
SELECT
  st.area,
  p.project_name,
  p.station_name,
  p.settle_start,
  p.settle_end,
  p.time_start,
  p.time_end,
  st.lon,
  st.lat
FROM paired p
LEFT JOIN t_station st
  ON st.name = p.project_name
WHERE (CAST(:area AS text) IS NULL OR st.area = CAST(:area AS text))
ORDER BY st.area, p.project_name, p.station_name
;