-- 运维：当前离线设备明细（最新 snapshot 批次，status=0）
-- schema: om_result；离线时长 = now() - data_collect_time
WITH latest AS (
  SELECT max(snapshot_time) AS snapshot_time
  FROM om_result.t_om_data_alarm_summary
)
SELECT
  o.type AS device_type,
  o.device_key,
  o.name AS device_name,
  o.area_name,
  o.data_collect_time,
  CASE
    WHEN o.data_collect_time IS NULL THEN NULL
    ELSE (now()::timestamp - o.data_collect_time::timestamp)
  END AS offline_duration,
  CASE
    WHEN o.data_collect_time IS NULL THEN NULL
    ELSE GREATEST(
      0,
      FLOOR(
        EXTRACT(EPOCH FROM (now()::timestamp - o.data_collect_time::timestamp)) / 86400.0
      )::int
    )
  END AS offline_days,
  o.snapshot_time
FROM om_result.t_om_offline_device_list o
INNER JOIN latest l ON o.snapshot_time = l.snapshot_time
WHERE o.status = 0
ORDER BY o.type, offline_days DESC NULLS LAST, o.name
