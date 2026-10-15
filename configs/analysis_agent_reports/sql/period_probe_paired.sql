-- 窗内是否存在至少 1 条初末 data_time 不同的配对（LIMIT 1）
-- 绑定：t_start, t_end, fcb_marks, jyb_marks, fcb_projects, jyb_projects, area

WITH fcb_raw AS (
  SELECT
    w.project_name,
    w.station_name,
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
  SELECT a.project_name
  FROM union_raw a
  JOIN union_raw b
    ON a.project_name = b.project_name
   AND a.station_name = b.station_name
   AND a.rn_asc = 1
   AND b.rn_desc = 1
  WHERE a.data_time <> b.data_time
)
SELECT 1 AS ok
FROM paired p
LEFT JOIN t_station st
  ON st.name = p.project_name
WHERE (CAST(:area AS text) IS NULL OR st.area = CAST(:area AS text))
LIMIT 1
;