-- 月报：本月设备离线告警次数（口径 C：COUNT(*)）与涉及监测站数
-- 窗：半开 [t_start, t_end)；站码 = name 截取「-」之前
-- 月窗汇总为 0 时回退：本表最新 snapshot_time 批次全量（仍 status=0）
WITH month_agg AS (
  SELECT
    COUNT(*)::int AS offline_alarm_count,
    COUNT(
      DISTINCT NULLIF(
        CASE
          WHEN position('-' IN COALESCE(o.name, '')) > 0
            THEN split_part(o.name, '-', 1)
          ELSE COALESCE(o.name, '')
        END,
        ''
      )
    )::int AS station_count
  FROM om_result.t_om_offline_device_list o
  WHERE o.snapshot_time >= CAST(:t_start AS timestamp)
    AND o.snapshot_time < CAST(:t_end AS timestamp)
    AND o.status = 0
),
latest AS (
  SELECT max(o.snapshot_time) AS snapshot_time
  FROM om_result.t_om_offline_device_list o
),
latest_agg AS (
  SELECT
    COUNT(*)::int AS offline_alarm_count,
    COUNT(
      DISTINCT NULLIF(
        CASE
          WHEN position('-' IN COALESCE(o.name, '')) > 0
            THEN split_part(o.name, '-', 1)
          ELSE COALESCE(o.name, '')
        END,
        ''
      )
    )::int AS station_count,
    l.snapshot_time
  FROM om_result.t_om_offline_device_list o
  INNER JOIN latest l ON o.snapshot_time = l.snapshot_time
  WHERE o.status = 0
  GROUP BY l.snapshot_time
)
SELECT
  CASE
    WHEN m.offline_alarm_count > 0 THEN m.offline_alarm_count
    ELSE COALESCE(l.offline_alarm_count, 0)
  END AS offline_alarm_count,
  CASE
    WHEN m.offline_alarm_count > 0 THEN m.station_count
    ELSE COALESCE(l.station_count, 0)
  END AS station_count,
  CASE
    WHEN m.offline_alarm_count > 0 THEN FALSE
    ELSE TRUE
  END AS month_offline_fallback,
  CASE
    WHEN m.offline_alarm_count > 0 THEN NULL
    ELSE l.snapshot_time
  END AS fallback_snapshot_time
FROM month_agg m
LEFT JOIN latest_agg l ON TRUE
