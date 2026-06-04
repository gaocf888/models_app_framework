-- 检修策略数据计划参考 SQL（analysis_plan_maintenance_strategy · q0～q5）
-- 对照：configs/prompts.yaml → analysis_plan_maintenance_strategy / analysis_synthesis_maintenance_strategy
-- 需求对齐：高温过热器寿命<6月→1级(根)；水冷壁轻微磨损→2级(处)；省煤器低风险→3级(根)
-- TiDB/MySQL 8；表名以部署 ANALYSIS_NL2SQL_TABLE_SCOPE / catalog 为准
-- 占位：
--   @unit_keyword   机组/锅炉名称关键字（空则全厂）
--   @t_start_1y     近1年窗起点（q1，测厚/换管佐证）
--   @t_start_3y     近3年窗起点（q2 遗留缺陷、q4 泄爆）
--   @t_start_6m     近6个月窗起点（q3 超温）
--   @t_start_5y     近5年窗起点（q5 日级时间轴）
--   @t_start_3m     近3个月窗起点（q5b 月度汇总）
--   @t_end          统计窗终点（开区间上界）
-- 轻微磨损口径：defect_type='2' 且 (mark_area<=50 或 描述/备注含「轻微」)
-- 低风险省煤器：residual_life>=12 月、wall_thickness_rate<0.15（减薄速率低，阈值可按厂修）、同排无磨损缺陷
-- 泄爆表名：结构文档 overhual_leakage；catalog 常写 overhaul_leakage，执行前请确认
-- 约束：每条 plan 问句单条可执行 SQL；禁止 WITH/CTE

-- =============================================================================
-- q0 统一检修优先级汇总（按级别+设备聚合；synthesis 2.1 清单主表）
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  agg.所属设备,
  agg.检修优先级,
  agg.数量,
  agg.数量单位,
  agg.判定条件
FROM (
  SELECT
    detail.boiler_id,
    detail.device_id,
    detail.所属设备,
    detail.检修优先级,
    detail.判定条件,
    COUNT(*) AS 数量,
    CASE
      WHEN detail.检修优先级 = '2级建议检' THEN '处'
      ELSE '根'
    END AS 数量单位
  FROM (
    SELECT DISTINCT
      tr.boiler_id,
      tr.device_id,
      asd.device_name AS 所属设备,
      '1级必检' AS 检修优先级,
      CONCAT('高温过热器剩余寿命', tr.residual_life, '个月<6个月') AS 判定条件,
      CONCAT(tr.device_id, '-', tr.row_num, '-', tr.pipe_num) AS dedup_key
    FROM overhaul_thickness_rate tr
    INNER JOIN account_static_device asd ON tr.device_id = asd.device_id
    WHERE (
        asd.device_name LIKE '%高温过热器%'
        OR asd.device_name LIKE '%高温%过热器%'
      )
      AND IFNULL(tr.residual_life, 999) < 6

    UNION ALL

    SELECT DISTINCT
      asd.boiler_id,
      r.device_id,
      asd.device_name AS 所属设备,
      '2级建议检' AS 检修优先级,
      '水冷壁轻微磨损区域' AS 判定条件,
      CONCAT(
        r.device_id, '-', r.row_num, '-',
        IFNULL(r.check_id, IFNULL(r.hole_code, ''))
      ) AS dedup_key
    FROM overhaul_record r
    INNER JOIN account_static_device asd ON r.device_id = asd.device_id
    WHERE r.del_flag = '0'
      AND r.mark_type = '2'
      AND r.defect_type = '2'
      AND asd.device_name LIKE '%水冷壁%'
      AND (
        IFNULL(r.mark_area, '') = ''
        OR CAST(r.mark_area AS DECIMAL(10, 2)) <= 50
        OR IFNULL(r.mark_area, '') LIKE '%轻微%'
        OR IFNULL(r.reserve1, '') LIKE '%轻微%'
        OR IFNULL(r.reserve2, '') LIKE '%轻微%'
      )

    UNION ALL

    SELECT DISTINCT
      tr.boiler_id,
      tr.device_id,
      asd.device_name AS 所属设备,
      '3级可暂缓' AS 检修优先级,
      '省煤器低风险管段' AS 判定条件,
      CONCAT(tr.device_id, '-', tr.row_num, '-', tr.pipe_num) AS dedup_key
    FROM overhaul_thickness_rate tr
    INNER JOIN account_static_device asd ON tr.device_id = asd.device_id
    WHERE asd.device_name LIKE '%省煤器%'
      AND IFNULL(tr.residual_life, 0) >= 12
      AND (IFNULL(tr.wall_thickness_rate, 0) < 0.15 OR tr.wall_thickness_rate IS NULL)
      AND NOT EXISTS (
        SELECT 1
        FROM overhaul_record rw
        WHERE rw.del_flag = '0'
          AND rw.device_id = tr.device_id
          AND rw.mark_type = '2'
          AND rw.defect_type = '2'
          AND CAST(IFNULL(rw.row_num, '0') AS SIGNED) = tr.row_num
      )
  ) detail
  GROUP BY
    detail.boiler_id,
    detail.device_id,
    detail.所属设备,
    detail.检修优先级,
    detail.判定条件
) agg
INNER JOIN account_boiler ab ON agg.boiler_id = ab.boiler_id
WHERE (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
ORDER BY
  CASE agg.检修优先级 WHEN '1级必检' THEN 1 WHEN '2级建议检' THEN 2 ELSE 3 END,
  ab.boiler_name,
  agg.所属设备;

-- =============================================================================
-- q1 测厚与换管数据汇总（近1年；无优先级 CASE；不 JOIN account_boiler 展示字段）
-- =============================================================================
SELECT
  asd.device_name AS 所属设备,
  r.row_num AS 管屏号,
  IFNULL(t.tube_position, t.tube_code) AS 管位置,
  IFNULL(pr.model, cl.model_info) AS 规格材质,
  CONCAT(
    '原始壁厚:', IFNULL(cl.wall_thickness, tr.wall_thickness),
    '; 原始外径:', IFNULL(cl.out_thickness, tr.out_thickness)
  ) AS 原始参数,
  MAX(tr.wall_thickness_rate) AS 最大减薄速率,
  MAX(IFNULL(t.thickness, tr.wall_thickness_measure)) AS 最新壁厚测量值,
  MAX(tr.residual_life) AS 剩余寿命_月,
  MAX(t.is_change) AS 是否已换管,
  COUNT(DISTINCT t.tube_id) AS 本组管段数量
FROM overhaul_record r
INNER JOIN overhaul_record_tubes t ON r.id = t.overhaul_record_id
INNER JOIN account_static_device asd ON r.device_id = asd.device_id
LEFT JOIN overhaul_new_checklocation cl ON t.location_id = cl.id
LEFT JOIN account_device_piperow pr
  ON r.device_id = pr.device_id
  AND CAST(r.row_num AS SIGNED) = pr.row_count
LEFT JOIN overhaul_thickness_rate tr
  ON tr.device_id = r.device_id
  AND tr.row_num = CAST(r.row_num AS SIGNED)
WHERE r.del_flag = '0'
  AND r.mark_type IN ('1', '2')
  AND r.mark_time >= @t_start_1y
  AND r.mark_time < @t_end
  AND (
    @unit_keyword IS NULL
    OR @unit_keyword = ''
    OR asd.boiler_id IN (
      SELECT boiler_id FROM account_boiler WHERE boiler_name LIKE CONCAT('%', @unit_keyword, '%')
    )
  )
GROUP BY
  asd.device_name,
  r.row_num,
  IFNULL(t.tube_position, t.tube_code),
  IFNULL(pr.model, cl.model_info),
  cl.wall_thickness,
  cl.out_thickness,
  tr.wall_thickness,
  tr.out_thickness
ORDER BY asd.device_name, r.row_num, 管位置;

-- =============================================================================
-- q2 历史遗留问题（缺陷）汇总（近3年；校验2级轻微磨损/未闭环）
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面设备,
  p.row_num AS 排号,
  p.pipe_num AS 管号,
  IFNULL(p.relative_position, '') AS 相对位置,
  COUNT(*) AS 问题数量,
  MAX(CASE
    WHEN IFNULL(p.problem_descrip, '') LIKE '%轻微%'
      OR IFNULL(p.problem_type, '') LIKE '%轻微%'
    THEN '是' ELSE '否'
  END) AS 是否轻微缺陷,
  MAX(CASE
    WHEN IFNULL(p.status, '') IN ('已闭环', '已关闭', '已完成', '关闭') THEN '已闭环'
    ELSE '未闭环'
  END) AS 闭环状态归类,
  GROUP_CONCAT(DISTINCT p.problem_descrip SEPARATOR '；') AS 问题现象描述汇总,
  GROUP_CONCAT(DISTINCT p.deal_content SEPARATOR '；') AS 处置维修方法汇总,
  GROUP_CONCAT(DISTINCT p.problem_type SEPARATOR '；') AS 原因分类汇总,
  GROUP_CONCAT(DISTINCT p.status SEPARATOR '；') AS 当前处理状态
FROM overhaul_legacy_problem p
INNER JOIN account_boiler ab ON p.boiler_id = ab.boiler_id
INNER JOIN account_static_device asd ON p.device_id = asd.device_id
WHERE p.record_time >= @t_start_3y
  AND p.record_time < @t_end
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY ab.boiler_name, asd.device_name, p.row_num, p.pipe_num, p.relative_position
ORDER BY 问题数量 DESC, ab.boiler_name, asd.device_name;

-- =============================================================================
-- q3 超温数据与运行参数关联汇总（近6个月；按锅炉+区域+测点）
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面区域,
  btp.row_num AS 管排号,
  btp.pipe_num AS 管号,
  GROUP_CONCAT(DISTINCT pb.pipebox_name SEPARATOR '；') AS 集箱信息,
  t.pi_code AS 测点编码,
  IFNULL(btp.point_name, t.pi_code) AS 测点名称,
  COUNT(*) AS 超温次数,
  ROUND(AVG(t.limit_duration), 0) AS 平均超时时长_秒,
  MAX(t.highest_temp) AS 历史最高温度_℃,
  ROUND(AVG(t.mw_value), 2) AS 超温期间平均负荷_MW,
  ROUND(AVG(t.steam_pressure_value), 2) AS 超温期间平均主汽压力_MPa
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code
LEFT JOIN account_device_pipebox pb ON t.device_id = pb.device_id
WHERE t.start_time >= @t_start_6m
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY
  ab.boiler_name,
  asd.device_name,
  btp.row_num,
  btp.pipe_num,
  t.pi_code,
  btp.point_name
ORDER BY 超温次数 DESC, 历史最高温度_℃ DESC;

-- =============================================================================
-- q4 泄爆数据汇总（近3年）
-- 表名：结构文档 overhual_leakage；若 catalog 为 overhaul_leakage 请替换 FROM 子句表名
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 设备位置,
  l.row_num AS 管排号,
  l.pipe_num AS 管号,
  IFNULL(l.leakage_descrip, CONCAT('第', l.row_num, '排第', l.pipe_num, '根')) AS 位置描述,
  COUNT(*) AS 泄爆频次,
  MIN(l.leakage_date) AS 首次泄爆时间,
  MAX(l.leakage_date) AS 最近泄爆时间,
  GROUP_CONCAT(DISTINCT l.leakage_reason SEPARATOR '；') AS 泄爆原因描述汇总,
  GROUP_CONCAT(DISTINCT l.handling_method SEPARATOR '；') AS 处理方法汇总,
  GROUP_CONCAT(DISTINCT l.reason_type SEPARATOR '；') AS 原因分类汇总
FROM overhual_leakage l
INNER JOIN account_boiler ab ON l.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON l.device_id = asd.device_id
WHERE l.leakage_date >= @t_start_3y
  AND l.leakage_date < @t_end
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
GROUP BY ab.boiler_name, asd.device_name, l.row_num, l.pipe_num, l.leakage_descrip
ORDER BY 泄爆频次 DESC, 最近泄爆时间 DESC;

-- =============================================================================
-- q5 综合时间轴-日级汇总（近5年；超温+泄爆+换管+启停）
-- =============================================================================
SELECT
  ev.发生日期,
  ev.锅炉名称,
  ev.所属区域,
  SUM(CASE WHEN ev.事件类型 = '超温' THEN 1 ELSE 0 END) AS 超温事件次数,
  SUM(CASE WHEN ev.事件类型 = '泄爆' THEN 1 ELSE 0 END) AS 泄爆事件次数,
  SUM(CASE WHEN ev.事件类型 = '换管' THEN 1 ELSE 0 END) AS 换管事件次数,
  SUM(CASE WHEN ev.事件类型 = '启停' THEN 1 ELSE 0 END) AS 启停事件次数,
  COUNT(*) AS 总事件数
FROM (
  SELECT
    DATE(t.start_time) AS 发生日期,
    ab.boiler_name AS 锅炉名称,
    IFNULL(asd.device_name, '未知区域') AS 所属区域,
    '超温' AS 事件类型
  FROM monitor_hotarea_temp t
  INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
  LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
  WHERE t.start_time >= @t_start_5y
    AND t.start_time < @t_end
    AND t.highest_temp > t.limit_temp
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))

  UNION ALL

  SELECT
    l.leakage_date AS 发生日期,
    ab.boiler_name AS 锅炉名称,
    IFNULL(asd.device_name, IFNULL(l.leakage_descrip, '未知区域')) AS 所属区域,
    '泄爆' AS 事件类型
  FROM overhual_leakage l
  INNER JOIN account_boiler ab ON l.boiler_id = ab.boiler_id
  LEFT JOIN account_static_device asd ON l.device_id = asd.device_id
  WHERE l.leakage_date >= @t_start_5y
    AND l.leakage_date < @t_end
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))

  UNION ALL

  SELECT
    DATE(r.mark_time) AS 发生日期,
    ab.boiler_name AS 锅炉名称,
    IFNULL(asd.device_name, '未知区域') AS 所属区域,
    '换管' AS 事件类型
  FROM overhaul_record r
  INNER JOIN overhaul_record_tubes rt ON r.id = rt.overhaul_record_id
  INNER JOIN account_static_device asd ON r.device_id = asd.device_id
  INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id
  WHERE r.del_flag = '0'
    AND rt.is_change = 1
    AND r.mark_time >= @t_start_5y
    AND r.mark_time < @t_end
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))

  UNION ALL

  SELECT
    DATE(IFNULL(ss.stop_date, ss.start_date)) AS 发生日期,
    ab.boiler_name AS 锅炉名称,
    '全炉' AS 所属区域,
    '启停' AS 事件类型
  FROM monitor_boiler_start_stop ss
  INNER JOIN account_boiler ab ON ss.boiler_id = ab.boiler_id
  WHERE IFNULL(ss.stop_date, ss.start_date) >= @t_start_5y
    AND IFNULL(ss.stop_date, ss.start_date) < @t_end
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
) ev
GROUP BY ev.发生日期, ev.锅炉名称, ev.所属区域
ORDER BY ev.发生日期 DESC, 总事件数 DESC;

-- =============================================================================
-- q5b 综合时间轴-近3个月月度汇总（方案影响分析时序；与 q5 配套）
-- =============================================================================
SELECT
  ev.统计月份,
  ev.锅炉名称,
  ev.所属区域,
  SUM(CASE WHEN ev.事件类型 = '超温' THEN 1 ELSE 0 END) AS 超温事件次数,
  SUM(CASE WHEN ev.事件类型 = '泄爆' THEN 1 ELSE 0 END) AS 泄爆事件次数,
  SUM(CASE WHEN ev.事件类型 = '换管' THEN 1 ELSE 0 END) AS 换管事件次数,
  SUM(CASE WHEN ev.事件类型 = '启停' THEN 1 ELSE 0 END) AS 启停事件次数,
  COUNT(*) AS 总事件数
FROM (
  SELECT
    DATE_FORMAT(t.start_time, '%Y-%m') AS 统计月份,
    ab.boiler_name AS 锅炉名称,
    IFNULL(asd.device_name, '未知区域') AS 所属区域,
    '超温' AS 事件类型
  FROM monitor_hotarea_temp t
  INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
  LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
  WHERE t.start_time >= @t_start_3m
    AND t.start_time < @t_end
    AND t.highest_temp > t.limit_temp
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))

  UNION ALL

  SELECT
    DATE_FORMAT(l.leakage_date, '%Y-%m') AS 统计月份,
    ab.boiler_name AS 锅炉名称,
    IFNULL(asd.device_name, IFNULL(l.leakage_descrip, '未知区域')) AS 所属区域,
    '泄爆' AS 事件类型
  FROM overhual_leakage l
  INNER JOIN account_boiler ab ON l.boiler_id = ab.boiler_id
  LEFT JOIN account_static_device asd ON l.device_id = asd.device_id
  WHERE l.leakage_date >= @t_start_3m
    AND l.leakage_date < @t_end
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))

  UNION ALL

  SELECT
    DATE_FORMAT(r.mark_time, '%Y-%m') AS 统计月份,
    ab.boiler_name AS 锅炉名称,
    IFNULL(asd.device_name, '未知区域') AS 所属区域,
    '换管' AS 事件类型
  FROM overhaul_record r
  INNER JOIN overhaul_record_tubes rt ON r.id = rt.overhaul_record_id
  INNER JOIN account_static_device asd ON r.device_id = asd.device_id
  INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id
  WHERE r.del_flag = '0'
    AND rt.is_change = 1
    AND r.mark_time >= @t_start_3m
    AND r.mark_time < @t_end
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))

  UNION ALL

  SELECT
    DATE_FORMAT(IFNULL(ss.stop_date, ss.start_date), '%Y-%m') AS 统计月份,
    ab.boiler_name AS 锅炉名称,
    '全炉' AS 所属区域,
    '启停' AS 事件类型
  FROM monitor_boiler_start_stop ss
  INNER JOIN account_boiler ab ON ss.boiler_id = ab.boiler_id
  WHERE IFNULL(ss.stop_date, ss.start_date) >= @t_start_3m
    AND IFNULL(ss.stop_date, ss.start_date) < @t_end
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
) ev
GROUP BY ev.统计月份, ev.锅炉名称, ev.所属区域
ORDER BY ev.统计月份 DESC, 总事件数 DESC;
