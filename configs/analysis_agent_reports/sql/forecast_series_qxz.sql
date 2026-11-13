-- 预测章：气象降水月合计序列（t_end 回溯 12 个月）；仅分层标∩气象名单，每场地取 station_name 倒序第一口
WITH picked AS (
  SELECT project_name, MAX(station_name) AS station_name
  FROM t_data_wash_qxz
  WHERE cardinality(CAST(:qxz_projects AS text[])) > 0
    AND project_name = ANY(CAST(:qxz_projects AS text[]))
    AND station_name IS NOT NULL
    AND btrim(station_name) <> ''
  GROUP BY project_name
)
SELECT
  w.project_name,
  COALESCE(w.station_name, w.project_name) AS station_name,
  date_trunc('month', w.data_time)::timestamp AS data_time,
  SUM(w.real_time_rain) AS real_time_rain,
  AVG(w.temp) AS temp
FROM t_data_wash_qxz w
LEFT JOIN picked p ON w.project_name = p.project_name
WHERE cardinality(CAST(:qxz_projects AS text[])) > 0
  AND w.project_name = ANY(CAST(:qxz_projects AS text[]))
  AND w.data_time >= (CAST(:t_end AS timestamp) - INTERVAL '12 months')
  AND w.data_time < CAST(:t_end AS timestamp)
  AND (
    (p.station_name IS NOT NULL AND w.station_name = p.station_name)
    OR p.station_name IS NULL
  )
GROUP BY w.project_name, COALESCE(w.station_name, w.project_name), date_trunc('month', w.data_time)
ORDER BY w.project_name, data_time
;
