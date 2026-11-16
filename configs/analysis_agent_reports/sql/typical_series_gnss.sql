-- GNSS 时序。准实时表 project_name 为工程总名，覆盖名单对齐 station_name（与 yaml 站名一致）。
SELECT
  w.station_name AS project_name,
  w.station_name,
  w.data_time,
  w.displacement_2d,
  w.displacement_3d
FROM t_data_wash_gnss w
WHERE cardinality(CAST(:gnss_projects AS text[])) > 0
  AND w.station_name = ANY(CAST(:gnss_projects AS text[]))
  AND w.data_time >= CAST(:t_start AS timestamp)
  AND w.data_time < CAST(:t_end AS timestamp)
ORDER BY w.station_name, w.data_time
;