-- 运维：各仪器类型在线率 + 完整率 + 有效率（最新 snapshot 批次）
-- schema: om_result；无绑定参数
WITH latest AS (
  SELECT max(snapshot_time) AS snapshot_time
  FROM om_result.t_om_data_alarm_summary
),
online AS (
  SELECT o.type, o.total, o.online, o.offline, o.online_rate, o.snapshot_time
  FROM om_result.t_om_device_online_by_type o
  INNER JOIN latest l ON o.snapshot_time = l.snapshot_time
),
complete AS (
  SELECT c.type, c.rate AS complete_rate
  FROM om_result.t_om_complete_rate_by_type c
  INNER JOIN latest l ON c.snapshot_time = l.snapshot_time
),
effective AS (
  SELECT e.type, e.rate AS effective_rate
  FROM om_result.t_om_effective_rate_by_type e
  INNER JOIN latest l ON e.snapshot_time = l.snapshot_time
)
SELECT
  o.type AS device_type,
  o.total,
  o.online,
  o.offline,
  o.online_rate,
  c.complete_rate,
  e.effective_rate,
  o.snapshot_time
FROM online o
LEFT JOIN complete c ON c.type = o.type
LEFT JOIN effective e ON e.type = o.type
ORDER BY o.type
