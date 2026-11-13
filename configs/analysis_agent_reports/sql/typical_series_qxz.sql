-- 气象降水时序；库内 project_name 常为工程总名，按 station_name 站码对齐覆盖名单（F21-气象站 → F21）
-- 兼容旧数据：project_name 已是 F21(西集) 时仍可命中
WITH coded AS (
  SELECT
    w.project_name,
    w.station_name,
    w.data_time,
    w.real_time_rain,
    w.temp,
    upper(substring(btrim(w.station_name) from '^([A-Za-z]+[0-9]+)')) AS site_code
  FROM t_data_wash_qxz w
  WHERE w.data_time >= CAST(:t_start AS timestamp)
    AND w.data_time < CAST(:t_end AS timestamp)
    AND w.station_name IS NOT NULL
    AND btrim(w.station_name) <> ''
    AND (
      (
        cardinality(CAST(:qxz_codes AS text[])) > 0
        AND upper(substring(btrim(w.station_name) from '^([A-Za-z]+[0-9]+)'))
          = ANY(CAST(:qxz_codes AS text[]))
      )
      OR (
        cardinality(CAST(:qxz_projects AS text[])) > 0
        AND w.project_name = ANY(CAST(:qxz_projects AS text[]))
      )
    )
),
picked AS (
  SELECT site_code, MAX(station_name) AS station_name
  FROM coded
  WHERE site_code IS NOT NULL AND site_code <> ''
  GROUP BY site_code
)
SELECT
  c.project_name,
  c.station_name,
  c.data_time,
  c.real_time_rain,
  c.temp,
  c.site_code
FROM coded c
INNER JOIN picked p
  ON c.site_code = p.site_code
 AND c.station_name = p.station_name
ORDER BY c.site_code, c.data_time
;
