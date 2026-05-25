-- 超温分析 v2 数据计划参考 SQL（TiDB/MySQL 8，表名以 fmfb catalog 为准）
-- 占位说明：@unit_keyword 由 NL2SQL 从用户问题解析（如 1号锅炉、#2机组）；@t_start/@t_end 为超温分析时间窗

-- =============================================================================
-- q1 一、报告基础信息（锅炉台账 + 超温测点分级统计）
-- =============================================================================
SELECT
  '锅炉台账' AS section,
  ab.boiler_name AS 机组名称,
  ab.boiler_model AS 锅炉型号,
  CAST(ab.edfh AS DECIMAL(10, 2)) AS 额定负荷_MW,
  NULL AS 超温测点总数,
  NULL AS 轻微超温数量,
  NULL AS 中度超温数量,
  NULL AS 严重超温数量
FROM account_boiler ab
WHERE ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
LIMIT 1;

SELECT
  '测点分级统计' AS section,
  ab.boiler_name AS 机组名称,
  NULL AS 锅炉型号,
  NULL AS 额定负荷_MW,
  COUNT(DISTINCT x.pi_code) AS 超温测点总数,
  SUM(CASE WHEN x.over_level = '轻微超温' THEN 1 ELSE 0 END) AS 轻微超温数量,
  SUM(CASE WHEN x.over_level = '中度超温' THEN 1 ELSE 0 END) AS 中度超温数量,
  SUM(CASE WHEN x.over_level = '严重超温' THEN 1 ELSE 0 END) AS 严重超温数量
FROM (
  SELECT
    t.pi_code,
    t.boiler_id,
    CASE
      WHEN MAX(t.highest_temp - t.limit_temp) >= 20 THEN '严重超温'
      WHEN MAX(t.highest_temp - t.limit_temp) >= 10 THEN '中度超温'
      WHEN MAX(t.highest_temp - t.limit_temp) >= 5 THEN '轻微超温'
      ELSE '正常'
    END AS over_level
  FROM monitor_hotarea_temp t
  WHERE t.start_time >= @t_start
    AND t.start_time < @t_end
    AND t.highest_temp > t.limit_temp
  GROUP BY t.pi_code, t.boiler_id, t.limit_temp
) x
INNER JOIN account_boiler ab ON x.boiler_id = ab.boiler_id
WHERE ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
  AND x.over_level <> '正常';

-- =============================================================================
-- q2 二、超温事件概况（测点时段 + 运行工况 + 分级清单 + 设计/实测极值）
-- =============================================================================
SELECT
  '测点时段' AS section,
  t.pi_code AS 测点编号,
  btp.point_name AS 测点名称,
  asd.device_name AS 受热面名称,
  MIN(t.start_time) AS 最早超温起始,
  MAX(t.end_time) AS 最晚超温结束,
  SUM(t.limit_duration) AS 超温总时长_秒,
  MAX(t.limit_duration) AS 单次最长超温_秒,
  NULL AS 当前负荷_MW,
  NULL AS 当前负荷_percent,
  NULL AS 主汽压力_MPa,
  NULL AS over_level,
  NULL AS 测点及位置列表,
  NULL AS 测点数量,
  NULL AS 实测最高壁温,
  NULL AS 最大超温差值,
  NULL AS 平均超温差值
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY t.pi_code, btp.point_name, asd.device_name
ORDER BY SUM(t.limit_duration) DESC;

SELECT
  '运行工况' AS section,
  NULL AS 测点编号,
  NULL AS 测点名称,
  ab.boiler_name AS 受热面名称,
  MIN(t.start_time) AS 最早超温起始,
  MAX(t.end_time) AS 最晚超温结束,
  NULL AS 超温总时长_秒,
  NULL AS 单次最长超温_秒,
  ROUND(AVG(t.mw_value), 2) AS 当前负荷_MW,
  ROUND(AVG(t.mw_value) / NULLIF(ab.edfh, 0) * 100, 2) AS 当前负荷_percent,
  ROUND(AVG(t.steam_pressure_value), 2) AS 主汽压力_MPa,
  NULL AS over_level,
  NULL AS 测点及位置列表,
  NULL AS 测点数量,
  NULL AS 实测最高壁温,
  NULL AS 最大超温差值,
  NULL AS 平均超温差值
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY ab.boiler_id, ab.boiler_name, ab.edfh;

-- =============================================================================
-- q3 三、超温数据统计（区域汇总 + 尖峰频次）
-- =============================================================================
SELECT
  '区域统计' AS section,
  ab.boiler_name AS 机组名称,
  asd.device_name AS 超温区域,
  COUNT(DISTINCT t.pi_code) AS 测点数量,
  MAX(t.highest_temp) AS 最高壁温_℃,
  MIN(t.highest_temp) AS 最低壁温_℃,
  ROUND(AVG(t.highest_temp), 1) AS 平均壁温_℃,
  ROUND(AVG(t.highest_temp - t.limit_temp), 1) AS 平均超温差值_℃,
  SUM(t.limit_duration) AS 累计超温时长_秒,
  NULL AS 瞬时尖峰超温次数,
  NULL AS 尖峰超温累计时长_秒
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY ab.boiler_name, asd.device_name
ORDER BY SUM(t.limit_duration) DESC;

SELECT
  '尖峰频次' AS section,
  t.pi_code AS 测点编号,
  CONCAT(IFNULL(asd.device_name, ''), '-', IFNULL(btp.point_name, '')) AS 测点名称,
  NULL AS 测点数量,
  NULL AS 最高壁温_℃,
  NULL AS 最低壁温_℃,
  NULL AS 平均壁温_℃,
  NULL AS 平均超温差值_℃,
  NULL AS 累计超温时长_秒,
  SUM(CASE WHEN t.highest_temp - t.limit_temp >= 15 THEN 1 ELSE 0 END) AS 瞬时尖峰超温次数,
  SUM(CASE WHEN t.highest_temp - t.limit_temp >= 15 THEN t.limit_duration ELSE 0 END) AS 尖峰超温累计时长_秒
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY t.pi_code, asd.device_name, btp.point_name
HAVING SUM(CASE WHEN t.highest_temp - t.limit_temp >= 15 THEN 1 ELSE 0 END) > 0
ORDER BY 瞬时尖峰超温次数 DESC, 尖峰超温累计时长_秒 DESC;

-- =============================================================================
-- q4 三、关联参数联动（壁温记录时序 + SIS 关联测点时序）
-- =============================================================================
SELECT
  '壁温时序' AS data_source,
  DATE_FORMAT(t.start_time, '%Y-%m-%d %H:%i') AS 采集时间,
  t.pi_code AS 测点编码,
  IFNULL(btp.point_name, t.pi_code) AS 测点名称,
  t.highest_temp AS 壁温_℃,
  t.mw_value AS 机组负荷_MW,
  t.steam_pressure_value AS 主汽压力_MPa,
  NULL AS 测点数值
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
ORDER BY t.start_time, t.pi_code;

SELECT
  'SIS时序' AS data_source,
  DATE_FORMAT(spd.data_time, '%Y-%m-%d %H:%i') AS 采集时间,
  spd.tag AS 测点编码,
  IFNULL(btp.point_name, spd.tag) AS 测点名称,
  NULL AS 壁温_℃,
  NULL AS 机组负荷_MW,
  NULL AS 主汽压力_MPa,
  spd.value AS 测点数值
FROM sis_pi_data spd
LEFT JOIN base_temp_point btp ON spd.tag = btp.point_code
WHERE spd.data_time >= @t_start
  AND spd.data_time < @t_end
  AND (
    IFNULL(btp.point_name, '') LIKE '%减温水%'
    OR IFNULL(btp.point_name, '') LIKE '%烟温%'
    OR IFNULL(btp.point_name, '') LIKE '%排烟%'
    OR IFNULL(btp.point_name, '') LIKE '%主汽压力%'
    OR IFNULL(btp.point_name, '') LIKE '%负荷%'
    OR IFNULL(btp.point_name, '') LIKE '%总风量%'
  )
ORDER BY spd.data_time, spd.tag;

-- =============================================================================
-- q5 七、历史缺陷/泄爆/换管 + 整改效果验证
-- =============================================================================
SELECT
  '遗留问题' AS record_type,
  ab.boiler_name AS 机组名称,
  d.device_name AS 涉及设备,
  b.overhaul_name AS 检修项目,
  p.problem_descrip AS 问题描述,
  p.deal_content AS 处理内容,
  p.status AS 状态,
  p.record_time AS 记录时间,
  NULL AS 已恢复严重超温数,
  NULL AS 剩余未恢复严重超温数,
  NULL AS 中轻度是否全部恢复,
  NULL AS 中轻度当前平均壁温
FROM overhaul_legacy_problem p
INNER JOIN account_boiler ab ON p.boiler_id = ab.boiler_id
LEFT JOIN account_static_device d ON p.device_id = d.device_id
LEFT JOIN overhaul_boiler b ON p.overhaul_id = b.overhaul_id
WHERE ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
  AND p.record_time >= DATE_SUB(@t_end, INTERVAL 1 YEAR)

UNION ALL

SELECT
  '泄漏记录' AS record_type,
  ab.boiler_name,
  d.device_name,
  NULL AS 检修项目,
  l.leakage_descrip AS 问题描述,
  l.handling_method AS 处理内容,
  l.reason_type AS 状态,
  l.leakage_date AS 记录时间,
  NULL, NULL, NULL, NULL
FROM overhual_leakage l
INNER JOIN account_boiler ab ON l.boiler_id = ab.boiler_id
LEFT JOIN account_static_device d ON l.device_id = d.device_id
WHERE ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
  AND l.leakage_date >= DATE_SUB(@t_end, INTERVAL 1 YEAR)

UNION ALL

SELECT
  '换管记录' AS record_type,
  ab.boiler_name,
  d.device_name,
  b.overhaul_name,
  CONCAT('缺陷类型:', IFNULL(r.defect_type, ''), ' 位置:', IFNULL(t.tube_position, '')) AS 问题描述,
  '已换管' AS 处理内容,
  r.defect_type AS 状态,
  r.mark_time AS 记录时间,
  NULL, NULL, NULL, NULL
FROM overhaul_record r
INNER JOIN overhaul_record_tubes t ON r.id = t.overhaul_record_id
INNER JOIN account_static_device d ON r.device_id = d.device_id
INNER JOIN account_boiler ab ON d.boiler_id = ab.boiler_id
LEFT JOIN overhaul_boiler b ON r.overhaul_id = b.overhaul_id
WHERE r.mark_type = '2'
  AND t.is_change = '1'
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
  AND r.mark_time >= DATE_SUB(@t_end, INTERVAL 1 YEAR)
ORDER BY 记录时间 DESC;

-- 整改效果验证（调控后时间窗，@t_after 默认为 @t_end 之后 12 小时，由 NL2SQL 按用户问题调整）
SELECT
  '效果验证' AS record_type,
  ab.boiler_name AS 机组名称,
  NULL AS 涉及设备,
  NULL AS 检修项目,
  NULL AS 问题描述,
  NULL AS 处理内容,
  NULL AS 状态,
  NULL AS 记录时间,
  SUM(CASE WHEN x.over_level = '严重超温' AND x.highest_temp <= x.limit_temp THEN 1 ELSE 0 END) AS 已恢复严重超温数,
  SUM(CASE WHEN x.over_level = '严重超温' AND x.highest_temp > x.limit_temp THEN 1 ELSE 0 END) AS 剩余未恢复严重超温数,
  CASE
    WHEN SUM(CASE WHEN x.over_level IN ('中度超温', '轻微超温') AND x.highest_temp > x.limit_temp THEN 1 ELSE 0 END) = 0
    THEN '是' ELSE '否'
  END AS 中轻度是否全部恢复,
  ROUND(AVG(CASE WHEN x.over_level IN ('中度超温', '轻微超温') THEN x.highest_temp END), 1) AS 中轻度当前平均壁温
FROM (
  SELECT
    t.pi_code,
    t.boiler_id,
    t.limit_temp,
    t.highest_temp,
    CASE
      WHEN (t.highest_temp - t.limit_temp) >= 20 THEN '严重超温'
      WHEN (t.highest_temp - t.limit_temp) >= 10 THEN '中度超温'
      WHEN (t.highest_temp - t.limit_temp) >= 5 THEN '轻微超温'
      ELSE '正常'
    END AS over_level
  FROM monitor_hotarea_temp t
  WHERE t.start_time >= @t_after
) x
INNER JOIN account_boiler ab ON x.boiler_id = ab.boiler_id
WHERE ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY ab.boiler_name;

-- =============================================================================
-- q6 九、附件（壁温趋势 + 多测点对照 + 历史同类对标-本次）
-- =============================================================================
SELECT
  '壁温趋势' AS section,
  t.pi_code AS 测点编号,
  btp.point_name AS 测点名称,
  asd.device_name AS 设备名称,
  t.start_time AS 采集时间,
  t.highest_temp AS 壁温值,
  t.limit_temp AS 限值,
  (t.highest_temp - t.limit_temp) AS 超温差值,
  NULL AS 历史超温日期,
  NULL AS 历史最大超温
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
WHERE t.start_time >= @t_start
  AND t.end_time <= @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
ORDER BY t.start_time, t.pi_code;

SELECT
  '多测点对照' AS section,
  t.pi_code AS 测点编号,
  CONCAT(ab.boiler_name, '-', IFNULL(asd.device_name, ''), '-', IFNULL(btp.point_name, '')) AS 测点位置,
  btd.over_hot_limit AS 设计壁温_℃,
  MAX(t.highest_temp) AS 实测最高壁温_℃,
  MAX(t.highest_temp) - btd.over_hot_limit AS 超温差值_℃,
  SUM(t.limit_duration) AS 累计持续时长_秒,
  NULL AS 采集时间,
  NULL AS 壁温值,
  NULL AS 限值,
  NULL AS 历史超温日期,
  NULL AS 历史最大超温
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN base_temp_device btd ON t.device_id = btd.device_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY t.pi_code, ab.boiler_name, asd.device_name, btp.point_name, btd.over_hot_limit
ORDER BY 超温差值_℃ DESC;

SELECT
  '历史同类对标' AS section,
  t.pi_code AS 测点编号,
  CONCAT(ab.boiler_name, '-', IFNULL(asd.device_name, '')) AS 测点位置,
  NULL AS 设计壁温_℃,
  MAX(t.highest_temp) AS 实测最高壁温_℃,
  MAX(t.highest_temp - t.limit_temp) AS 超温差值_℃,
  SUM(t.limit_duration) AS 累计持续时长_秒,
  NULL AS 采集时间,
  NULL AS 壁温值,
  NULL AS 限值,
  DATE_FORMAT(MIN(t.start_time), '%Y-%m-%d') AS 历史超温日期,
  MAX(t.highest_temp - t.limit_temp) AS 历史最大超温
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
WHERE t.start_time < @t_start
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
  AND t.pi_code IN (
    SELECT DISTINCT t2.pi_code
    FROM monitor_hotarea_temp t2
    INNER JOIN account_boiler ab2 ON t2.boiler_id = ab2.boiler_id
    WHERE t2.start_time >= @t_start
      AND t2.start_time < @t_end
      AND t2.highest_temp > t2.limit_temp
      AND ab2.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
  )
GROUP BY t.pi_code, ab.boiler_name, asd.device_name, DATE(t.start_time)
ORDER BY 历史最大超温 DESC
LIMIT 50;
