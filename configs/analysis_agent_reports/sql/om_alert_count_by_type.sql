-- 运维：各仪器类型告警设备台数（绑定 :snapshot_time 批次，按 device_id 去重）
-- schema: om_result
SELECT
  a.type AS device_type,
  COUNT(DISTINCT a.device_id) AS alarm_device_count,
  a.snapshot_time
FROM om_result.t_om_device_alarm_list a
WHERE a.snapshot_time = CAST(:snapshot_time AS timestamp)
GROUP BY a.type, a.snapshot_time
ORDER BY a.type
