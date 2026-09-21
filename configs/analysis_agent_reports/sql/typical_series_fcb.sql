-- 层位 0 分层标日序列（典型曲线左轴）；仅 layer0 标号 ∩ fcb 覆盖
SELECT
  w.project_name,
  w.station_name,
  w.data_time,
  w.total_settle
FROM t_data_wash_fcb w
WHERE cardinality(CAST(:fcb_marks AS text[])) > 0
  AND w.station_name = ANY(CAST(:fcb_marks AS text[]))
  AND w.project_name = ANY(CAST(:fcb_projects AS text[]))
  AND w.data_time >= CAST(:t_start AS timestamp)
  AND w.data_time < CAST(:t_end AS timestamp)
  AND w.total_settle IS NOT NULL
  AND (:area IS NULL OR EXISTS (
    SELECT 1 FROM t_station st
    WHERE st.name = w.project_name AND st.area = CAST(:area AS text)
  ))
ORDER BY w.project_name, w.station_name, w.data_time
;