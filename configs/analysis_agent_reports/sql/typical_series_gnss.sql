-- GNSS 时序占位。device_station_map.gnss 一期为空：compose 不引用本文件。
-- 有官方名单后再挂月报/季报 compose，禁止用「事实表出现过的 project_name」冒充覆盖。
SELECT
  w.project_name,
  w.data_time,
  w.displacement_2d,
  w.displacement_3d
FROM t_data_wash_gnss w
WHERE cardinality(CAST(:gnss_projects AS text[])) > 0
  AND w.project_name = ANY(CAST(:gnss_projects AS text[]))
  AND w.data_time >= CAST(:t_start AS timestamp)
  AND w.data_time < CAST(:t_end AS timestamp)
ORDER BY w.project_name, w.data_time
;