-- 运维：当前告警仪器列表（绑定 :snapshot_time 批次）
-- schema: om_result；告警持续时长 = snapshot_time - off_time；序号对齐模版表1
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
    ELSE (CAST(:snapshot_time AS timestamp) - a.off_time::timestamp)
  END AS alarm_duration,
  a.snapshot_time
FROM om_result.t_om_device_alarm_list a
WHERE a.snapshot_time = CAST(:snapshot_time AS timestamp)
ORDER BY a.type, a.device_name
