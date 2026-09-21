-- 气象降水时序；仅 qxz 覆盖名单，禁止用极值站冒充其它场地降水
SELECT
  w.project_name,
  w.data_time,
  w.real_time_rain,
  w.temp
FROM t_data_wash_qxz w
WHERE cardinality(CAST(:qxz_projects AS text[])) > 0
  AND w.project_name = ANY(CAST(:qxz_projects AS text[]))
  AND w.data_time >= CAST(:t_start AS timestamp)
  AND w.data_time < CAST(:t_end AS timestamp)
ORDER BY w.project_name, w.data_time
;