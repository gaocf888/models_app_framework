-- 运维：告警总览 1 行（最新 snapshot 批次）
WITH latest AS (
  SELECT max(snapshot_time) AS snapshot_time
  FROM om_result.t_om_data_alarm_summary
)
SELECT
  s.total_count,
  s.alarm_count,
  s.normal_count,
  s.week_avg,
  s.month_avg,
  s.snapshot_time
FROM om_result.t_om_data_alarm_summary s
INNER JOIN latest l ON s.snapshot_time = l.snapshot_time
