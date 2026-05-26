-- 超温分析 v2 数据计划参考 SQL（方案B：q1 + q2a～q2d + q3a～q6d 共 15 条）
-- 对照：火电厂锅炉专业大模型系统-锅炉管壁超温智能分析报告-数据获取逻辑梳理0525.docx
-- TiDB/MySQL 8；表名以部署 ANALYSIS_NL2SQL_TABLE_SCOPE 为准
-- 占位：@unit_keyword 机组关键字；@t_start/@t_end 超温事件窗；@t_after 调控后跟踪窗起始（通常 = @t_end）
-- 超温等级（与 prompts v2 / synthesis v2 一致，按监测超温差值 highest_temp - limit_temp）：
--   严重 ≥20℃；中度 [10,20)；轻微 [5,10)；尖峰频次阈值 ≥15℃
-- 约束：每条 plan 问句单条可执行 SQL；禁止 WITH/CTE；limit_temp 取自 monitor_hotarea_temp

-- =============================================================================
-- q1 一、报告基础信息（锅炉台账 + 监测部位 + 超温测点分级统计）
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  ab.boiler_model AS 锅炉型号,
  CAST(ab.edfh AS DECIMAL(10, 2)) AS 额定负荷_MW,
  COALESCE(lv.超温测点总数, 0) AS 超温测点总数,
  COALESCE(lv.轻微超温数量, 0) AS 轻微超温数量,
  COALESCE(lv.中度超温数量, 0) AS 中度超温数量,
  COALESCE(lv.严重超温数量, 0) AS 严重超温数量,
  COALESCE(reg.监测部位, '') AS 监测部位
FROM account_boiler ab
LEFT JOIN (
  SELECT
    x.boiler_id,
    COUNT(*) AS 超温测点总数,
    SUM(CASE WHEN x.over_level = '轻微超温' THEN 1 ELSE 0 END) AS 轻微超温数量,
    SUM(CASE WHEN x.over_level = '中度超温' THEN 1 ELSE 0 END) AS 中度超温数量,
    SUM(CASE WHEN x.over_level = '严重超温' THEN 1 ELSE 0 END) AS 严重超温数量
  FROM (
    SELECT
      t.boiler_id,
      t.pi_code,
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
    GROUP BY t.boiler_id, t.pi_code
  ) x
  WHERE x.over_level <> '正常'
  GROUP BY x.boiler_id
) lv ON lv.boiler_id = ab.boiler_id
LEFT JOIN (
  SELECT
    d.boiler_id,
    GROUP_CONCAT(
      CONCAT(d.device_name, '（', d.point_cnt, '个测点）')
      ORDER BY d.point_cnt DESC, d.device_name
      SEPARATOR '、'
    ) AS 监测部位
  FROM (
    SELECT
      t.boiler_id,
      asd.device_id,
      asd.device_name,
      COUNT(DISTINCT t.pi_code) AS point_cnt
    FROM monitor_hotarea_temp t
    INNER JOIN account_static_device asd ON t.device_id = asd.device_id
    WHERE t.start_time >= @t_start
      AND t.start_time < @t_end
      AND t.highest_temp > t.limit_temp
    GROUP BY t.boiler_id, asd.device_id, asd.device_name
  ) d
  GROUP BY d.boiler_id
) reg ON reg.boiler_id = ab.boiler_id
WHERE ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
LIMIT 1;

-- =============================================================================
-- q2a 二、超温事件概况-1 测点超温起止时段（按测点一行）
-- =============================================================================
SELECT
  t.pi_code AS 测点编号,
  btp.point_name AS 测点名称,
  asd.device_name AS 受热面名称,
  btp.row_num AS 排号,
  btp.pipe_num AS 管号,
  MIN(t.start_time) AS 最早超温起始,
  MAX(t.end_time) AS 最晚超温结束,
  SUM(t.limit_duration) AS 超温总时长_秒,
  MAX(t.limit_duration) AS 单次最长超温_秒,
  CONCAT(
    MIN(t.start_time), ' 至 ', MAX(t.end_time),
    '，持续 ', SUM(t.limit_duration), ' 秒'
  ) AS 时段说明,
  ROUND(AVG(t.mw_value), 2) AS 平均负荷_MW,
  ROUND(AVG(t.mw_value) / NULLIF(ab.edfh, 0) * 100, 2) AS 负荷_percent,
  ROUND(AVG(t.steam_pressure_value), 2) AS 主汽压力_MPa,
  MAX(t.highest_temp) AS 实测最高壁温,
  MAX(t.highest_temp - t.limit_temp) AS 最大监测超温差值,
  MAX(t.highest_temp - IFNULL(btd.over_hot_limit, t.limit_temp)) AS 最大设计超温差值,
  CASE
    WHEN MAX(t.highest_temp - t.limit_temp) >= 20 THEN '严重超温'
    WHEN MAX(t.highest_temp - t.limit_temp) >= 10 THEN '中度超温'
    WHEN MAX(t.highest_temp - t.limit_temp) >= 5 THEN '轻微超温'
    ELSE '正常'
  END AS 超温等级,
  CONCAT(
    t.pi_code, '（位置：', IFNULL(ab.boiler_name, ''), '-',
    IFNULL(asd.device_name, ''), '-', IFNULL(btp.point_name, ''),
    IF(btp.row_num IS NOT NULL, CONCAT('，第', btp.row_num, '排'), ''),
    IF(btp.pipe_num IS NOT NULL, CONCAT('第', btp.pipe_num, '根'), ''),
    '）'
  ) AS 测点及位置
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN base_temp_device btd ON t.device_id = btd.device_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY
  t.pi_code, btp.point_name, btp.row_num, btp.pipe_num,
  asd.device_name, ab.boiler_name, btd.over_hot_limit
ORDER BY SUM(t.limit_duration) DESC;

-- =============================================================================
-- q2b 二、超温事件概况-2 全事件运行工况（单行）
-- =============================================================================
SELECT
  ROUND(AVG(t.mw_value), 2) AS 全事件平均负荷_MW,
  ROUND(AVG(t.mw_value) / NULLIF(MAX(ab.edfh), 0) * 100, 2) AS 全事件负荷_percent,
  ROUND(AVG(t.steam_pressure_value), 2) AS 全事件主汽压力_MPa
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY ab.boiler_id;

-- =============================================================================
-- q2c 二、超温事件概况-3 按严重度汇总测点
-- =============================================================================
SELECT
  x.over_level AS 超温等级,
  GROUP_CONCAT(x.测点及位置 ORDER BY x.max_delta DESC SEPARATOR '、') AS 测点及位置列表,
  COUNT(*) AS 测点数量
FROM (
  SELECT
    t.pi_code,
    CASE
      WHEN MAX(t.highest_temp - t.limit_temp) >= 20 THEN '严重超温'
      WHEN MAX(t.highest_temp - t.limit_temp) >= 10 THEN '中度超温'
      WHEN MAX(t.highest_temp - t.limit_temp) >= 5 THEN '轻微超温'
      ELSE '正常'
    END AS over_level,
    MAX(t.highest_temp - t.limit_temp) AS max_delta,
    CONCAT(
      t.pi_code, '（位置：', IFNULL(MAX(ab.boiler_name), ''), '-',
      IFNULL(MAX(asd.device_name), ''), '-', IFNULL(MAX(btp.point_name), ''), '）'
    ) AS 测点及位置
  FROM monitor_hotarea_temp t
  INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
  LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
  LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
  WHERE t.start_time >= @t_start
    AND t.start_time < @t_end
    AND t.highest_temp > t.limit_temp
    AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
  GROUP BY t.pi_code
) x
WHERE x.over_level <> '正常'
GROUP BY x.over_level
ORDER BY FIELD(x.over_level, '严重超温', '中度超温', '轻微超温');

-- =============================================================================
-- q2d 二、超温事件概况-4 设计壁温与实测极值（单行）
-- =============================================================================
SELECT
  dev.分区域设计壁温,
  ev.全事件实测最高壁温,
  ev.全事件最高壁温测点,
  ev.全事件最大超温差值_监测,
  ev.全事件最大超温差值_设计,
  ev.全事件平均超温差值_监测
FROM account_boiler ab
INNER JOIN (
  SELECT
    t2.boiler_id,
    MAX(t2.highest_temp) AS 全事件实测最高壁温,
    SUBSTRING_INDEX(
      GROUP_CONCAT(
        CONCAT(t2.pi_code, '（', IFNULL(btp2.point_name, ''), '）')
        ORDER BY t2.highest_temp DESC
        SEPARATOR ','
      ),
      ',',
      1
    ) AS 全事件最高壁温测点,
    MAX(t2.highest_temp - t2.limit_temp) AS 全事件最大超温差值_监测,
    MAX(t2.highest_temp - IFNULL(btd2.over_hot_limit, t2.limit_temp)) AS 全事件最大超温差值_设计,
    ROUND(AVG(t2.highest_temp - t2.limit_temp), 2) AS 全事件平均超温差值_监测
  FROM monitor_hotarea_temp t2
  LEFT JOIN base_temp_point btp2 ON t2.pi_code = btp2.point_code
  LEFT JOIN base_temp_device btd2 ON t2.device_id = btd2.device_id
  WHERE t2.start_time >= @t_start
    AND t2.start_time < @t_end
    AND t2.highest_temp > t2.limit_temp
  GROUP BY t2.boiler_id
) ev ON ev.boiler_id = ab.boiler_id
LEFT JOIN (
  SELECT
    t3.boiler_id,
    GROUP_CONCAT(
      DISTINCT CONCAT(
        IFNULL(asd3.device_type, ''), '（', IFNULL(asd3.device_name, ''), '）：',
        IFNULL(btd3.over_hot_limit, ''), '℃'
      )
      ORDER BY asd3.device_name
      SEPARATOR '；'
    ) AS 分区域设计壁温
  FROM monitor_hotarea_temp t3
  INNER JOIN account_static_device asd3 ON t3.device_id = asd3.device_id
  LEFT JOIN base_temp_device btd3 ON t3.device_id = btd3.device_id
  WHERE t3.start_time >= @t_start
    AND t3.start_time < @t_end
    AND t3.highest_temp > t3.limit_temp
  GROUP BY t3.boiler_id
) dev ON dev.boiler_id = ab.boiler_id
WHERE ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%');

-- s02_1～s02_5 由 synthesis v2 对 q2a/q2b/q2c/q2d 确定性模板回填。

-- =============================================================================
-- q3a 三、超温数据统计-区域汇总
-- =============================================================================
SELECT
  ab.boiler_name AS 机组名称,
  asd.device_name AS 超温区域,
  COUNT(DISTINCT t.pi_code) AS 测点数量,
  MAX(t.highest_temp) AS 最高壁温_℃,
  MIN(t.highest_temp) AS 最低壁温_℃,
  ROUND(AVG(t.highest_temp), 1) AS 平均壁温_℃,
  ROUND(AVG(t.highest_temp - t.limit_temp), 1) AS 平均超温差值_℃,
  SUM(t.limit_duration) AS 累计超温时长_秒
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
GROUP BY ab.boiler_name, asd.device_name
ORDER BY SUM(t.limit_duration) DESC;

-- =============================================================================
-- q3b 三、超温数据统计-尖峰频次
-- =============================================================================
SELECT
  t.pi_code AS 测点编号,
  CONCAT(IFNULL(asd.device_name, ''), '-', IFNULL(btp.point_name, '')) AS 测点名称,
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
-- q4a 三、关联参数联动-壁温时序
-- =============================================================================
SELECT
  DATE_FORMAT(t.start_time, '%Y-%m-%d %H:%i') AS 采集时间,
  t.pi_code AS 测点编号,
  IFNULL(btp.point_name, t.pi_code) AS 测点名称,
  t.highest_temp AS 壁温_℃,
  t.limit_temp AS 限温值,
  t.mw_value AS 机组负荷_MW,
  t.steam_pressure_value AS 主汽压力_MPa
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
ORDER BY t.start_time, t.pi_code;

-- =============================================================================
-- q4b 三、关联参数联动-SIS 关联参数时序
-- =============================================================================
SELECT
  DATE_FORMAT(spd.data_time, '%Y-%m-%d %H:%i') AS 采集时间,
  spd.tag AS 测点编码,
  IFNULL(btp.point_name, spd.tag) AS 测点名称,
  spd.value AS 测点数值
FROM sis_pi_data spd
LEFT JOIN base_temp_point btp ON spd.tag = btp.point_code
WHERE spd.data_time >= @t_start
  AND spd.data_time < @t_end
  AND (
    spd.tag IN (
      SELECT DISTINCT t_evt.pi_code
      FROM monitor_hotarea_temp t_evt
      INNER JOIN account_boiler ab_evt ON t_evt.boiler_id = ab_evt.boiler_id
      WHERE t_evt.start_time >= @t_start
        AND t_evt.start_time < @t_end
        AND t_evt.highest_temp > t_evt.limit_temp
        AND ab_evt.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
    )
    OR IFNULL(btp.point_name, '') LIKE '%减温水%'
    OR IFNULL(btp.point_name, '') LIKE '%烟温%'
    OR IFNULL(btp.point_name, '') LIKE '%排烟%'
    OR IFNULL(btp.point_name, '') LIKE '%主汽压力%'
    OR IFNULL(btp.point_name, '') LIKE '%负荷%'
    OR IFNULL(btp.point_name, '') LIKE '%总风量%'
  )
ORDER BY spd.data_time, spd.tag;

-- =============================================================================
-- q5a 七、历史缺陷/泄爆/换管记录
-- =============================================================================
SELECT
  '遗留问题' AS record_type,
  ab.boiler_name AS 机组名称,
  d.device_name AS 涉及设备,
  b.overhaul_name AS 检修项目,
  p.problem_descrip AS 问题描述,
  p.deal_content AS 处理内容,
  p.status AS 状态,
  p.record_time AS 记录时间
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
  l.leakage_date AS 记录时间
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
  r.mark_time AS 记录时间
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

-- =============================================================================
-- q5b 七、整改效果验证汇总（单行；事件窗定级 + 跟踪窗判恢复，对齐 0525 docx）
-- =============================================================================
SELECT
  SUM(
    CASE
      WHEN ep.event_level = '严重超温'
        AND COALESCE(ap.after_max_highest_temp, ep.event_limit_temp + 1) <= ep.event_limit_temp
      THEN 1 ELSE 0
    END
  ) AS 已恢复严重超温数,
  MAX(
    CASE
      WHEN ep.event_level = '严重超温'
        AND COALESCE(ap.after_max_highest_temp, ep.event_limit_temp + 1) <= ep.event_limit_temp
      THEN ap.after_max_highest_temp
    END
  ) AS 恢复后最高壁温,
  SUM(
    CASE
      WHEN ep.event_level = '严重超温'
        AND COALESCE(ap.after_max_highest_temp, ep.event_limit_temp + 1) > ep.event_limit_temp
      THEN 1 ELSE 0
    END
  ) AS 剩余未恢复严重超温数,
  CASE
    WHEN SUM(
      CASE
        WHEN ep.event_level IN ('中度超温', '轻微超温')
          AND COALESCE(ap.after_max_highest_temp, ep.event_limit_temp + 1) > ep.event_limit_temp
        THEN 1 ELSE 0
      END
    ) = 0
    THEN '是' ELSE '否'
  END AS 中轻度是否全部恢复,
  ROUND(
    AVG(
      CASE
        WHEN ep.event_level IN ('中度超温', '轻微超温') THEN ap.after_max_highest_temp
      END
    ),
    1
  ) AS 中轻度当前平均壁温
FROM (
  SELECT
    t.pi_code,
    t.boiler_id,
    MAX(t.limit_temp) AS event_limit_temp,
    CASE
      WHEN MAX(t.highest_temp - t.limit_temp) >= 20 THEN '严重超温'
      WHEN MAX(t.highest_temp - t.limit_temp) >= 10 THEN '中度超温'
      WHEN MAX(t.highest_temp - t.limit_temp) >= 5 THEN '轻微超温'
      ELSE '正常'
    END AS event_level
  FROM monitor_hotarea_temp t
  INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
  WHERE t.start_time >= @t_start
    AND t.start_time < @t_end
    AND t.highest_temp > t.limit_temp
    AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
  GROUP BY t.pi_code, t.boiler_id
) ep
LEFT JOIN (
  SELECT
    t.pi_code,
    t.boiler_id,
    MAX(t.highest_temp) AS after_max_highest_temp
  FROM monitor_hotarea_temp t
  WHERE t.start_time >= @t_after
  GROUP BY t.pi_code, t.boiler_id
) ap ON ap.pi_code = ep.pi_code AND ap.boiler_id = ep.boiler_id
WHERE ep.event_level <> '正常';

-- =============================================================================
-- q6a 九、附件-壁温趋势
-- =============================================================================
SELECT
  t.pi_code AS 测点编号,
  btp.point_name AS 测点名称,
  asd.device_name AS 设备名称,
  t.start_time AS 采集时间,
  t.highest_temp AS 壁温值,
  t.limit_temp AS 限值,
  (t.highest_temp - t.limit_temp) AS 超温差值
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
WHERE t.start_time >= @t_start
  AND t.end_time <= @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
ORDER BY t.start_time, t.pi_code;

-- =============================================================================
-- q6b 九、附件-多测点对照
-- =============================================================================
SELECT
  t.pi_code AS 测点编号,
  CONCAT(ab.boiler_name, '-', IFNULL(asd.device_name, ''), '-', IFNULL(btp.point_name, '')) AS 测点位置,
  btd.over_hot_limit AS 设计壁温_℃,
  MAX(t.highest_temp) AS 实测最高壁温_℃,
  MAX(t.highest_temp) - btd.over_hot_limit AS 超温差值_℃,
  SUM(t.limit_duration) AS 累计持续时长_秒
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

-- =============================================================================
-- q6c 九、附件-历史同类对标
-- =============================================================================
SELECT
  t.pi_code AS 测点编号,
  CONCAT(ab.boiler_name, '-', IFNULL(asd.device_name, '')) AS 测点位置,
  DATE_FORMAT(MIN(t.start_time), '%Y-%m-%d') AS 历史超温日期,
  MAX(t.highest_temp - t.limit_temp) AS 历史最大超温差值,
  SUM(t.limit_duration) AS 累计持续时长_秒
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
ORDER BY 历史最大超温差值 DESC
LIMIT 50;

-- =============================================================================
-- q6d 九、附件-DCS参数联动趋势图（0525 docx：壁温+负荷+主汽压 + SIS 减温水/烟温等）
-- 长表格式便于 s12b 多序列折线图；亦兼容 docx 宽表字段名（壁温_℃/机组负荷_MW/主汽压力_MPa）
-- =============================================================================
SELECT
  DATE_FORMAT(t.start_time, '%Y-%m-%d %H:%i') AS 采集时间,
  t.pi_code AS 测点编号,
  IFNULL(btp.point_name, t.pi_code) AS 测点名称,
  '壁温' AS 参数类型,
  CAST(t.highest_temp AS DECIMAL(10, 2)) AS 参数值,
  '℃' AS 单位
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')

UNION ALL

SELECT
  DATE_FORMAT(t.start_time, '%Y-%m-%d %H:%i'),
  t.pi_code,
  IFNULL(btp.point_name, t.pi_code),
  '机组负荷',
  CAST(t.mw_value AS DECIMAL(10, 2)),
  'MW'
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')

UNION ALL

SELECT
  DATE_FORMAT(t.start_time, '%Y-%m-%d %H:%i'),
  t.pi_code,
  IFNULL(btp.point_name, t.pi_code),
  '主汽压力',
  CAST(t.steam_pressure_value AS DECIMAL(10, 2)),
  'MPa'
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')

UNION ALL

SELECT
  DATE_FORMAT(spd.data_time, '%Y-%m-%d %H:%i'),
  spd.tag,
  IFNULL(btp.point_name, spd.tag),
  CASE
    WHEN IFNULL(btp.point_name, '') LIKE '%减温水%' THEN '减温水'
    WHEN IFNULL(btp.point_name, '') LIKE '%烟温%' THEN '烟温'
    WHEN IFNULL(btp.point_name, '') LIKE '%排烟%' THEN '排烟温度'
    WHEN IFNULL(btp.point_name, '') LIKE '%主汽压力%' THEN '主汽压力(SIS)'
    WHEN IFNULL(btp.point_name, '') LIKE '%负荷%' THEN '机组负荷(SIS)'
    WHEN IFNULL(btp.point_name, '') LIKE '%总风量%' THEN '总风量'
    ELSE 'DCS参数'
  END,
  CAST(spd.value AS DECIMAL(10, 2)),
  ''
FROM sis_pi_data spd
LEFT JOIN base_temp_point btp ON spd.tag = btp.point_code
WHERE spd.data_time >= @t_start
  AND spd.data_time < @t_end
  AND (
    spd.tag IN (
      SELECT DISTINCT t_evt.pi_code
      FROM monitor_hotarea_temp t_evt
      INNER JOIN account_boiler ab_evt ON t_evt.boiler_id = ab_evt.boiler_id
      WHERE t_evt.start_time >= @t_start
        AND t_evt.start_time < @t_end
        AND t_evt.highest_temp > t_evt.limit_temp
        AND ab_evt.boiler_name LIKE CONCAT('%', @unit_keyword, '%')
    )
    OR IFNULL(btp.point_name, '') LIKE '%减温水%'
    OR IFNULL(btp.point_name, '') LIKE '%烟温%'
    OR IFNULL(btp.point_name, '') LIKE '%排烟%'
    OR IFNULL(btp.point_name, '') LIKE '%主汽压力%'
    OR IFNULL(btp.point_name, '') LIKE '%负荷%'
    OR IFNULL(btp.point_name, '') LIKE '%总风量%'
  )
ORDER BY 采集时间, 测点编号, 参数类型;
