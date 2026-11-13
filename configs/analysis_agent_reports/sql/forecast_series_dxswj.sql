-- 预测章：地下水位时序（t_end 回溯 12 个月）；仅分层标∩地下水公共集，每场地取 station_name 倒序第一口
WITH picked AS (
  SELECT project_name, MAX(station_name) AS station_name
  FROM t_data_wash_dxswj
  WHERE cardinality(CAST(:dxswj_projects AS text[])) > 0
    AND project_name = ANY(CAST(:dxswj_projects AS text[]))
    AND station_name IS NOT NULL
    AND btrim(station_name) <> ''
  GROUP BY project_name
)
SELECT
  w.project_name,
  COALESCE(w.station_name, w.project_name) AS station_name,
  w.data_time,
  w.elevation,
  w.deep
FROM t_data_wash_dxswj w
LEFT JOIN picked p ON w.project_name = p.project_name
WHERE cardinality(CAST(:dxswj_projects AS text[])) > 0
  AND w.project_name = ANY(CAST(:dxswj_projects AS text[]))
  AND w.data_time >= (CAST(:t_end AS timestamp) - INTERVAL '12 months')
  AND w.data_time < CAST(:t_end AS timestamp)
  AND w.elevation IS NOT NULL
  AND (
    (p.station_name IS NOT NULL AND w.station_name = p.station_name)
    OR p.station_name IS NULL
  )
ORDER BY w.project_name, w.data_time
;
