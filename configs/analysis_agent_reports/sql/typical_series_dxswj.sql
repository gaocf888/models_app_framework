-- 地下水位时序；WHERE 必须带 dxswj 覆盖 allowlist（:dxswj_projects）
SELECT
  w.project_name,
  w.data_time,
  w.elevation,
  w.deep
FROM t_data_wash_dxswj w
WHERE cardinality(CAST(:dxswj_projects AS text[])) > 0
  AND w.project_name = ANY(CAST(:dxswj_projects AS text[]))
  AND w.data_time >= CAST(:t_start AS timestamp)
  AND w.data_time < CAST(:t_end AS timestamp)
  AND w.elevation IS NOT NULL
ORDER BY w.project_name, w.data_time
;