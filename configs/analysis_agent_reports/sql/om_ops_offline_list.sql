-- 运维：当前离线设备明细（绑定 :snapshot_time 批次，status=0）
-- schema: om_result；离线时长 = snapshot_time - data_collect_time
SELECT
  o.type AS device_type,
  o.device_key,
  o.name AS device_name,
  o.area_name,
  o.data_collect_time,
  CASE
    WHEN o.data_collect_time IS NULL THEN NULL
    ELSE (CAST(:snapshot_time AS timestamp) - o.data_collect_time::timestamp)
  END AS offline_duration,
  CASE
    WHEN o.data_collect_time IS NULL THEN NULL
    ELSE GREATEST(
      0,
      FLOOR(
        EXTRACT(EPOCH FROM (CAST(:snapshot_time AS timestamp) - o.data_collect_time::timestamp)) / 86400.0
      )::int
    )
  END AS offline_days,
  o.snapshot_time
FROM om_result.t_om_offline_device_list o
WHERE o.snapshot_time = CAST(:snapshot_time AS timestamp)
  AND o.status = 0
ORDER BY o.type, offline_days DESC NULLS LAST, o.name
