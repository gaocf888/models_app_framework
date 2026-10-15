-- 运维：当前告警仪器列表（最新 snapshot 批次）
-- schema: om_result；告警持续时长 = now() - off_time；序号对齐模版表1
WITH latest AS (
  SELECT max(snapshot_time) AS snapshot_time
  FROM om_result.t_om_data_alarm_summary
)
SELECT
  ROW_NUMBER() OVER (ORDER BY a.type, a.device_name) AS seq,
  a.device_name,
  a.type AS device_type,
  a.area_name,
  a.ss_value,
  a.content,
  a.off_time,
  a.create_time,
  CASE
    WHEN a.off_time IS NULL THEN NULL
    ELSE (now()::timestamp - a.off_time::timestamp)
  END AS alarm_duration,
  a.snapshot_time
FROM om_result.t_om_device_alarm_list a
INNER JOIN latest l ON a.snapshot_time = l.snapshot_time
ORDER BY a.type, a.device_name
