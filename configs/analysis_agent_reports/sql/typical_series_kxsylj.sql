-- 孔隙水压力时序；仅 kxsylj 覆盖名单
SELECT
  w.project_name,
  w.data_time,
  w.pressure
FROM t_data_wash_kxsylj w
WHERE cardinality(CAST(:kxsylj_projects AS text[])) > 0
  AND w.project_name = ANY(CAST(:kxsylj_projects AS text[]))
  AND w.data_time >= CAST(:t_start AS timestamp)
  AND w.data_time < CAST(:t_end AS timestamp)
  AND w.pressure IS NOT NULL
ORDER BY w.project_name, w.data_time
;