-- 运维：告警总览 1 行（绑定 :snapshot_time 批次）
SELECT
  s.total_count,
  s.alarm_count,
  s.normal_count,
  s.week_avg,
  s.month_avg,
  s.snapshot_time
FROM om_result.t_om_data_alarm_summary s
WHERE s.snapshot_time = CAST(:snapshot_time AS timestamp)
