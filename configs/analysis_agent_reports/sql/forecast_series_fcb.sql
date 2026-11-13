-- 预测章：层位 0 分层标月均序列（t_end 回溯 12 个月）；仅 layer0 标号 ∩ fcb 覆盖
-- 库端按月聚合，避免日序列超 max_rows_per_query(默认2000) 截断丢站
SELECT
  w.project_name,
  w.station_name,
  date_trunc('month', w.data_time)::timestamp AS data_time,
  AVG(w.total_settle) AS total_settle
FROM t_data_wash_fcb w
WHERE cardinality(CAST(:fcb_marks AS text[])) > 0
  AND w.station_name = ANY(CAST(:fcb_marks AS text[]))
  AND w.project_name = ANY(CAST(:fcb_projects AS text[]))
  AND w.data_time >= (CAST(:t_end AS timestamp) - INTERVAL '12 months')
  AND w.data_time < CAST(:t_end AS timestamp)
  AND w.total_settle IS NOT NULL
  AND (CAST(:area AS text) IS NULL OR EXISTS (
    SELECT 1 FROM t_station st
    WHERE st.name = w.project_name AND st.area = CAST(:area AS text)
  ))
GROUP BY w.project_name, w.station_name, date_trunc('month', w.data_time)
ORDER BY w.project_name, w.station_name, data_time
;
