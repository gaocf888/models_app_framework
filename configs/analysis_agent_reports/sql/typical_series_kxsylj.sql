-- 孔隙水压力时序；仅分层标∩孔压名单，每场地只取 station_name 倒序第一口
WITH picked AS (
  SELECT project_name, MAX(station_name) AS station_name
  FROM t_data_wash_kxsylj
  WHERE cardinality(CAST(:kxsylj_projects AS text[])) > 0
    AND project_name = ANY(CAST(:kxsylj_projects AS text[]))
    AND station_name IS NOT NULL
    AND btrim(station_name) <> ''
  GROUP BY project_name
)
SELECT
  w.project_name,
  COALESCE(w.station_name, w.project_name) AS station_name,
  w.data_time,
  w.pressure
FROM t_data_wash_kxsylj w
LEFT JOIN picked p ON w.project_name = p.project_name
WHERE cardinality(CAST(:kxsylj_projects AS text[])) > 0
  AND w.project_name = ANY(CAST(:kxsylj_projects AS text[]))
  AND w.data_time >= CAST(:t_start AS timestamp)
  AND w.data_time < CAST(:t_end AS timestamp)
  AND w.pressure IS NOT NULL
  AND (
    (p.station_name IS NOT NULL AND w.station_name = p.station_name)
    OR p.station_name IS NULL
  )
ORDER BY w.project_name, w.data_time
;
