-- 运维：各仪器类型在线率 + 完整率 + 有效率（绑定 :snapshot_time 批次）
-- schema: om_result
SELECT
  o.type AS device_type,
  o.total,
  o.online,
  o.offline,
  o.online_rate,
  c.complete_rate,
  e.effective_rate,
  o.snapshot_time
FROM om_result.t_om_device_online_by_type o
LEFT JOIN om_result.t_om_complete_rate_by_type c
  ON c.type = o.type AND c.snapshot_time = o.snapshot_time
LEFT JOIN om_result.t_om_effective_rate_by_type e
  ON e.type = o.type AND e.snapshot_time = o.snapshot_time
WHERE o.snapshot_time = CAST(:snapshot_time AS timestamp)
ORDER BY o.type
