-- 运维：各仪器类型告警设备台数（最新 snapshot 批次，按 device_id 去重）
-- schema: om_result
WITH latest AS (
  SELECT max(snapshot_time) AS snapshot_time
  FROM om_result.t_om_data_alarm_summary
)
SELECT
  a.type AS device_type,
  COUNT(DISTINCT a.device_id) AS alarm_device_count,
  a.snapshot_time
FROM om_result.t_om_device_alarm_list a
INNER JOIN latest l ON a.snapshot_time = l.snapshot_time
GROUP BY a.type, a.snapshot_time
ORDER BY a.type
