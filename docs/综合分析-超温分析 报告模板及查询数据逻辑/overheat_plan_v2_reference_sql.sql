-- 超温分析 v2 数据计划参考 SQL（20260602 四段式模板；q1～q7）
-- 对照：模板---锅炉管壁超温智能分析报告-最新.docx
-- TiDB/MySQL 8；表名以部署 ANALYSIS_NL2SQL_TABLE_SCOPE 为准
-- 占位：@unit_keyword 机组关键字（空则全厂）；@t_start/@t_end 超温事件窗
-- 异常等级（监测超温差值 highest_temp - limit_temp）：
--   Ⅰ [5,10) ℃；Ⅱ [10,20) ℃；Ⅲ ≥20 ℃；Ⅳ ≥40 ℃（临界爆管）
-- 约束：每条 plan 问句单条可执行 SQL；禁止 WITH/CTE

-- =============================================================================
-- q0 超温事件时间包络（按机组：最早开始 / 最晚结束，供概览章起止时间展示）
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  DATE_FORMAT(MIN(t.start_time), '%Y.%m.%d %H:%i:%s') AS 最早超温开始时间,
  DATE_FORMAT(MAX(IFNULL(t.end_time, t.start_time)), '%Y.%m.%d %H:%i:%s') AS 最晚超温结束时间
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY ab.boiler_name
ORDER BY ab.boiler_name;

-- =============================================================================
-- q1 超温情况概览-测点明细（按日表 / 周详情共用）
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  CONCAT(
    IFNULL(asd.device_name, '未知区域'),
    ' 限', IFNULL(btd.over_hot_limit, t.limit_temp), '℃'
  ) AS 区域名称,
  adp.model AS 规格材质,
  t.pi_code AS 测点编号,
  IFNULL(btp.point_name, t.pi_code) AS 测点名称,
  MAX(t.highest_temp) AS 最大超温值_℃,
  MIN(t.highest_temp) AS 最小超温值_℃,
  ROUND(MAX(t.limit_duration) / 60, 0) AS 最大连续超温时长_分钟,
  DATE_FORMAT(MIN(t.start_time), '%Y.%m.%d %H:%i:%s') AS 超温日期,
  MAX(t.highest_temp - t.limit_temp) AS 最大监测超温差值_℃,
  CASE
    WHEN MAX(t.highest_temp - t.limit_temp) >= 40 THEN 'Ⅳ级（临界爆管）'
    WHEN MAX(t.highest_temp - t.limit_temp) >= 20 THEN 'Ⅲ级（严重超温）'
    WHEN MAX(t.highest_temp - t.limit_temp) >= 10 THEN 'Ⅱ级（中度超温）'
    WHEN MAX(t.highest_temp - t.limit_temp) >= 5  THEN 'Ⅰ级（轻微超温）'
    ELSE '正常'
  END AS 异常等级,
  asd.device_name AS 受热面名称
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN base_temp_device btd ON t.device_id = btd.device_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
LEFT JOIN account_device_piperow adp ON t.device_id = adp.device_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY
  ab.boiler_name, asd.device_name, btd.over_hot_limit, t.limit_temp,
  t.pi_code, btp.point_name
ORDER BY ab.boiler_name, 最大监测超温差值_℃ DESC;

-- =============================================================================
-- q2 超温情况概览-周区域概览
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  CONCAT(
    IFNULL(asd.device_name, '未知区域'),
    ' 限', IFNULL(MAX(btd.over_hot_limit), MAX(t.limit_temp)), '℃'
  ) AS 区域名称,
  COUNT(DISTINCT t.pi_code) AS 超温点数,
  MAX(t.highest_temp) AS 周最大超温值_℃,
  MIN(t.highest_temp) AS 周最小超温值_℃,
  ROUND(MAX(t.limit_duration) / 60, 0) AS 周最大连续超温时长_分钟,
  SUM(CASE WHEN pt.max_delta >= 5  AND pt.max_delta < 10 THEN 1 ELSE 0 END) AS Ⅰ级数量,
  SUM(CASE WHEN pt.max_delta >= 10 AND pt.max_delta < 20 THEN 1 ELSE 0 END) AS Ⅱ级数量,
  SUM(CASE WHEN pt.max_delta >= 20 AND pt.max_delta < 40 THEN 1 ELSE 0 END) AS Ⅲ级数量,
  SUM(CASE WHEN pt.max_delta >= 40 THEN 1 ELSE 0 END) AS Ⅳ级数量,
  asd.device_name AS 受热面名称
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN base_temp_device btd ON t.device_id = btd.device_id
INNER JOIN (
  SELECT pi_code, device_id, MAX(highest_temp - limit_temp) AS max_delta
  FROM monitor_hotarea_temp
  WHERE start_time >= @t_start AND start_time < @t_end AND highest_temp > limit_temp
  GROUP BY pi_code, device_id
) pt ON pt.pi_code = t.pi_code AND pt.device_id = t.device_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY ab.boiler_name, asd.device_name
ORDER BY ab.boiler_name, 周最大连续超温时长_分钟 DESC;

-- =============================================================================
-- q3 超温情况概览-周趋势按日
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  asd.device_name AS 受热面名称,
  DATE(t.start_time) AS 超温日期,
  COUNT(DISTINCT t.pi_code) AS 当日超温测点数
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY ab.boiler_name, asd.device_name, DATE(t.start_time)
ORDER BY ab.boiler_name, asd.device_name, 超温日期;

-- =============================================================================
-- q4 超温原因剖析-全事件运行工况
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  ROUND(AVG(t.mw_value), 2) AS 平均负荷_MW,
  ROUND(AVG(t.mw_value) / NULLIF(MAX(ab.edfh), 0) * 100, 2) AS 负荷_percent,
  ROUND(AVG(t.steam_pressure_value), 2) AS 平均主汽压力_MPa
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY ab.boiler_id, ab.boiler_name;

-- =============================================================================
-- q5 超温原因剖析-SIS/DCS 关联参数汇总（按测点+参数类型聚合）
-- 说明：
--   - 时间窗：超温事件 MIN(start)-30min ~ MAX(end)+30min（对齐业务「超温前后30min」）
--   - 壁温 tag 常见 HAD/HAC/HAH + CT（如 10HAH11CT101），须全部排除，勿只排除 HAD+CT
--   - tag 编码为主；LEFT JOIN base_dev_pi_b 用 pi_name_ch 兜底（炉膛烟温/减温水/氧量/总风量）
--   - 机组负荷、总给煤量由 q4/q7 承担，不在 q5 纳入
--   - 烟温/排烟/总风量/氧量/炉膛负压若库中无对应测点 → 报告写「待补充」
-- =============================================================================
SELECT
  CASE
    WHEN UPPER(spd.tag) LIKE '%FW%'
      OR UPPER(spd.tag) LIKE '%CWF%'
      OR UPPER(spd.tag) LIKE '%DSW%'
      OR UPPER(spd.tag) LIKE '%DESUP%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%减温水%' THEN '减温水'
    WHEN UPPER(spd.tag) LIKE '%FGT%'
      OR UPPER(spd.tag) LIKE '%FLUE%'
      OR UPPER(spd.tag) LIKE '%GAS%TEMP%'
      OR UPPER(spd.tag) LIKE '%GGT%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%炉膛烟温%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%烟温%' THEN '烟温'
    WHEN UPPER(spd.tag) LIKE '%EXH%'
      OR UPPER(spd.tag) LIKE '%STACK%'
      OR UPPER(spd.tag) LIKE '%ESP%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%排烟%' THEN '排烟'
    WHEN UPPER(spd.tag) LIKE '%TAF%'
      OR UPPER(spd.tag) LIKE '%PAF%'
      OR UPPER(spd.tag) LIKE '%SAF%'
      OR UPPER(spd.tag) LIKE '%AIR%FLOW%'
      OR UPPER(spd.tag) LIKE '%TOTAL%AIR%'
      OR pi.pi_name_ch = '总风量' THEN '总风量'
    WHEN UPPER(spd.tag) LIKE '%O2%'
      OR UPPER(spd.tag) LIKE '%OXY%'
      OR UPPER(spd.tag) LIKE '%OXYGEN%'
      OR pi.pi_name_ch = '炉膛出口氧量' THEN '氧量'
    WHEN UPPER(spd.tag) LIKE '%DRFT%'
      OR UPPER(spd.tag) LIKE '%FDRAFT%'
      OR UPPER(spd.tag) LIKE '%FURN%P%'
      OR UPPER(spd.tag) LIKE '%FURNACE%PRESS%'
      OR IFNULL(pi.pi_name_ch, '') LIKE '%炉膛负压%' THEN '炉膛负压'
    ELSE '其他关联参数'
  END AS 参数类型,
  spd.tag AS 测点编码,
  MAX(IFNULL(pi.pi_name_ch, spd.tag)) AS 测点名称,
  COUNT(*) AS 采样点数,
  COUNT(DISTINCT DATE(spd.data_time)) AS 采样天数,
  ROUND(MIN(spd.value), 2) AS 最小值,
  ROUND(MAX(spd.value), 2) AS 最大值,
  ROUND(AVG(spd.value), 2) AS 平均值,
  DATE_FORMAT(MIN(spd.data_time), '%Y.%m.%d %H:%i:%s') AS 首次采样时间,
  DATE_FORMAT(MAX(spd.data_time), '%Y.%m.%d %H:%i:%s') AS 末次采样时间
FROM sis_pi_data spd
LEFT JOIN base_dev_pi_b pi ON pi.pi_code = spd.tag
INNER JOIN (
  SELECT
    MIN(DATE_SUB(t.start_time, INTERVAL 30 MINUTE)) AS evt_min,
    MAX(DATE_ADD(IFNULL(t.end_time, t.start_time), INTERVAL 30 MINUTE)) AS evt_max
  FROM monitor_hotarea_temp t
  INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
  WHERE t.start_time >= @t_start
    AND t.start_time < @t_end
    AND t.highest_temp > t.limit_temp
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
) evt ON spd.data_time >= evt.evt_min AND spd.data_time <= evt.evt_max
WHERE NOT (
  UPPER(spd.tag) LIKE '%CT%'
  AND (
    UPPER(spd.tag) LIKE '%HAD%'
    OR UPPER(spd.tag) LIKE '%HAC%'
    OR UPPER(spd.tag) LIKE '%HAH%'
  )
)
AND (
  UPPER(spd.tag) LIKE '%FW%'
  OR UPPER(spd.tag) LIKE '%CWF%'
  OR UPPER(spd.tag) LIKE '%DSW%'
  OR UPPER(spd.tag) LIKE '%DESUP%'
  OR UPPER(spd.tag) LIKE '%FGT%'
  OR UPPER(spd.tag) LIKE '%FLUE%'
  OR UPPER(spd.tag) LIKE '%GAS%TEMP%'
  OR UPPER(spd.tag) LIKE '%GGT%'
  OR UPPER(spd.tag) LIKE '%EXH%'
  OR UPPER(spd.tag) LIKE '%STACK%'
  OR UPPER(spd.tag) LIKE '%ESP%'
  OR UPPER(spd.tag) LIKE '%TAF%'
  OR UPPER(spd.tag) LIKE '%PAF%'
  OR UPPER(spd.tag) LIKE '%SAF%'
  OR UPPER(spd.tag) LIKE '%AIR%FLOW%'
  OR UPPER(spd.tag) LIKE '%TOTAL%AIR%'
  OR UPPER(spd.tag) LIKE '%O2%'
  OR UPPER(spd.tag) LIKE '%OXY%'
  OR UPPER(spd.tag) LIKE '%OXYGEN%'
  OR UPPER(spd.tag) LIKE '%DRFT%'
  OR UPPER(spd.tag) LIKE '%FDRAFT%'
  OR UPPER(spd.tag) LIKE '%FURN%P%'
  OR UPPER(spd.tag) LIKE '%FURNACE%PRESS%'
  OR IFNULL(pi.pi_name_ch, '') LIKE '%减温水%'
  OR IFNULL(pi.pi_name_ch, '') LIKE '%炉膛烟温%'
  OR IFNULL(pi.pi_name_ch, '') LIKE '%烟温%'
  OR IFNULL(pi.pi_name_ch, '') LIKE '%排烟%'
  OR IFNULL(pi.pi_name_ch, '') LIKE '%炉膛负压%'
  OR pi.pi_name_ch IN ('炉膛出口氧量', '总风量')
)
GROUP BY 参数类型, spd.tag
HAVING 参数类型 <> '其他关联参数'
ORDER BY 参数类型, 采样点数 DESC, 测点编码;

-- =============================================================================
-- q6 超温原因剖析-吹灰区域汇总（按机组+受热面聚合，非逐条明细）
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  IFNULL(asd.device_name, '未知受热面') AS 受热面名称,
  COUNT(*) AS 吹灰次数,
  COUNT(DISTINCT DATE(r.start_time)) AS 吹灰天数,
  ROUND(SUM(r.blowing_duration) / 60, 0) AS 总吹灰时长_分钟,
  ROUND(AVG(r.blowing_duration) / 60, 1) AS 平均吹灰时长_分钟,
  DATE_FORMAT(MIN(r.start_time), '%Y.%m.%d %H:%i:%s') AS 首次吹灰时间,
  DATE_FORMAT(MAX(r.start_time), '%Y.%m.%d %H:%i:%s') AS 末次吹灰时间
FROM monitor_soot_blower_run_record r
INNER JOIN base_soot_blower sb ON r.blower_id = sb.blower_id
INNER JOIN account_boiler ab ON sb.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON sb.device_id = asd.device_id
WHERE r.start_time >= @t_start
  AND r.start_time < @t_end
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY ab.boiler_name, asd.device_name
ORDER BY ab.boiler_name, 吹灰次数 ASC, 受热面名称;

-- =============================================================================
-- q7 超温原因剖析-磨煤机运行汇总（按机组+磨煤机聚合，非逐条明细）
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  IFNULL(cm.mill_name, '未知磨煤机') AS 磨煤机名称,
  COUNT(*) AS 采样记录数,
  COUNT(DISTINCT DATE(r.record_time)) AS 运行天数,
  ROUND(AVG(r.coal_flow_tonh), 2) AS 平均给煤量_t_h,
  ROUND(MAX(r.coal_flow_tonh), 2) AS 最大给煤量_t_h,
  ROUND(AVG(r.primary_air_flow), 2) AS 平均一次风量,
  ROUND(AVG(r.boiler_mw), 2) AS 平均负荷_MW,
  ROUND(MAX(r.boiler_mw), 2) AS 最大负荷_MW,
  ROUND(MIN(r.boiler_mw), 2) AS 最小负荷_MW,
  DATE_FORMAT(MIN(r.record_time), '%Y.%m.%d %H:%i:%s') AS 首次记录时间,
  DATE_FORMAT(MAX(r.record_time), '%Y.%m.%d %H:%i:%s') AS 末次记录时间
FROM monitor_coal_mill_run_record r
INNER JOIN base_coal_mill cm ON r.mill_id = cm.mill_id
INNER JOIN account_boiler ab ON cm.boiler_id = ab.boiler_id
WHERE r.record_time >= @t_start
  AND r.record_time < @t_end
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY ab.boiler_name, cm.mill_name
ORDER BY ab.boiler_name, 采样记录数 ASC, 磨煤机名称;
