-- 请求窗之前最近一次有效监测时刻（半开上界 t_end）
-- 绑定：t_end, fcb_marks, jyb_marks, fcb_projects, jyb_projects, area

SELECT MAX(u.data_time) AS max_data_time
FROM (
  SELECT w.data_time
  FROM t_data_wash_fcb w
  LEFT JOIN t_station st ON st.name = w.project_name
  WHERE cardinality(CAST(:fcb_marks AS text[])) > 0
    AND w.station_name = ANY(CAST(:fcb_marks AS text[]))
    AND w.project_name = ANY(CAST(:fcb_projects AS text[]))
    AND w.data_time < CAST(:t_end AS timestamp)
    AND w.total_settle IS NOT NULL
    AND (CAST(:area AS text) IS NULL OR st.area = CAST(:area AS text))
  UNION ALL
  SELECT w.data_time
  FROM t_data_wash_jyb w
  LEFT JOIN t_station st ON st.name = w.project_name
  WHERE cardinality(CAST(:jyb_marks AS text[])) > 0
    AND w.station_name = ANY(CAST(:jyb_marks AS text[]))
    AND w.project_name = ANY(CAST(:jyb_projects AS text[]))
    AND w.data_time < CAST(:t_end AS timestamp)
    AND w.total_settle IS NOT NULL
    AND (CAST(:area AS text) IS NULL OR st.area = CAST(:area AS text))
) u
;