-- 运维：先取窗末日 [snap_day_start, snap_day_end) 最后一批 snapshot_time；无则全局最新
-- 批次钟：om_result.t_om_data_alarm_summary（与原先 latest CTE 同源）
WITH day_hit AS (
  SELECT max(s.snapshot_time) AS snapshot_time
  FROM om_result.t_om_data_alarm_summary s
  WHERE s.snapshot_time >= CAST(:snap_day_start AS timestamp)
    AND s.snapshot_time < CAST(:snap_day_end AS timestamp)
),
picked AS (
  SELECT
    COALESCE(
      (SELECT snapshot_time FROM day_hit),
      (SELECT max(snapshot_time) FROM om_result.t_om_data_alarm_summary)
    ) AS snapshot_time,
    CASE
      WHEN (SELECT snapshot_time FROM day_hit) IS NULL THEN TRUE
      ELSE FALSE
    END AS ops_fallback
)
SELECT snapshot_time, ops_fallback
FROM picked
