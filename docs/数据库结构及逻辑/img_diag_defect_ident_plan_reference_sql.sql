-- 缺陷识别看图诊断数据计划参考 SQL（analysis_plan_img_diag_defect_ident · q1～q5）
-- 对照：configs/prompts.yaml → analysis_plan_img_diag_defect_ident / analysis_synthesis_img_diag_defect_ident
-- 数据库：fmfb · TiDB/MySQL 8 兼容
-- 占位符（NL2SQL 基座从用户问题解析后改写）：
--   @unit_keyword    锅炉名称关键字（空/NULL 则不过滤锅炉）
--   @device_keyword  受热面/设备名称关键字
--   @piperow_keyword 管排名称关键字
--   @row_no          排数（NULL 则跳过排数过滤）
--   @tube_no         管数/管号（NULL 则跳过管号过滤）
--   @t_start         时间窗起点（含）
--   @t_end           时间窗终点（不含，开区间上界）
-- 约束：每条 plan 问句对应单条可执行 SQL；禁止 WITH/CTE

-- =============================================================================
-- q1 管段基础参数（规格材质、壁厚/胀粗/壁温限值、运行时长）
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面名称,
  adr.piperow_name AS 管排名称,
  adr.model AS 规格材质,
  adr.piperow_diameter AS 管直径,
  adr.piperow_thickness AS 设计管壁厚度,
  adr.row_count AS 台账排数,
  adr.pipe_count AS 台账管数,
  apb.design_pressure AS 集箱设计压力,
  apb.design_temp AS 集箱设计温度,
  tr.wall_thickness_limit AS 壁厚限值,
  tr.out_thickness_limit AS 外径胀粗限值,
  tr.out_thickness_measure AS 测量外径,
  tr.out_thickness_rate AS 蠕胀速率,
  tr.wall_thickness_measure AS 最近测量壁厚,
  tr.last_measure_date AS 最近测厚日期,
  btp.limit_temp AS 壁温限值,
  ab.run_date AS 投产日期,
  DATEDIFF(CURDATE(), ab.run_date) AS 累计运行天数,
  (
    SELECT ROUND(SUM(TIMESTAMPDIFF(HOUR, ss.start_date, IFNULL(ss.stop_date, NOW()))) / 24.0, 1)
    FROM monitor_boiler_start_stop ss
    WHERE ss.boiler_id = ab.boiler_id
      AND ss.start_date >= @t_start
      AND ss.start_date < @t_end
  ) AS 统计窗内运行小时折算天
FROM account_boiler ab
INNER JOIN account_static_device asd ON ab.boiler_id = asd.boiler_id
LEFT JOIN account_device_piperow adr ON asd.device_id = adr.device_id
LEFT JOIN account_device_pipebox apb ON asd.device_id = apb.device_id
LEFT JOIN base_temp_point btp ON btp.device_id = asd.device_id
  AND (@row_no IS NULL OR btp.row_num = @row_no)
  AND (@tube_no IS NULL OR btp.pipe_num = @tube_no)
LEFT JOIN overhaul_thickness_rate tr ON tr.boiler_id = ab.boiler_id
  AND tr.device_id = asd.device_id
  AND (@row_no IS NULL OR tr.row_num = @row_no)
  AND (@tube_no IS NULL OR tr.pipe_num = @tube_no)
WHERE (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@piperow_keyword IS NULL OR @piperow_keyword = '' OR adr.piperow_name LIKE CONCAT('%', @piperow_keyword, '%'))
ORDER BY ab.boiler_name, asd.device_name, adr.piperow_name
LIMIT 200;

-- =============================================================================
-- q2 检修处置历史（近3次壁厚、遗留问题、减薄速率、泄爆、补焊、换管）
-- =============================================================================
-- q2-1 近 3 次测厚记录（mark_type=1）
SELECT
  'thickness' AS 记录类型,
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面,
  r.row_num AS 排数,
  rt.thickness AS 壁厚值,
  r.mark_time AS 记录时间,
  ob.overhaul_name AS 检修名称,
  NULL AS 问题描述,
  NULL AS 处置内容,
  NULL AS 泄爆原因,
  IFNULL(rt.is_change, 0) AS 是否换管
FROM (
  SELECT r.id, r.device_id, r.row_num, r.mark_time, r.overhaul_id
  FROM overhaul_record r
  INNER JOIN account_static_device asd0 ON r.device_id = asd0.device_id
  INNER JOIN account_boiler ab0 ON asd0.boiler_id = ab0.boiler_id
  WHERE r.del_flag = '0'
    AND r.mark_type = '1'
    AND r.mark_time >= @t_start
    AND r.mark_time < @t_end
    AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab0.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
    AND (@device_keyword IS NULL OR @device_keyword = '' OR asd0.device_name LIKE CONCAT('%', @device_keyword, '%'))
    AND (@row_no IS NULL OR CAST(IFNULL(r.row_num, '0') AS SIGNED) = @row_no)
  ORDER BY r.mark_time DESC
  LIMIT 3
) recent_thickness
INNER JOIN overhaul_record r ON r.id = recent_thickness.id
INNER JOIN overhaul_record_tubes rt ON r.id = rt.overhaul_record_id
INNER JOIN account_static_device asd ON r.device_id = asd.device_id
INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id
LEFT JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id

UNION ALL

SELECT
  'legacy_problem' AS 记录类型,
  ab.boiler_name,
  asd.device_name,
  lp.row_num,
  NULL,
  lp.record_time,
  ob.overhaul_name,
  lp.problem_descrip,
  lp.deal_content,
  NULL,
  NULL
FROM overhaul_legacy_problem lp
INNER JOIN account_boiler ab ON lp.boiler_id = ab.boiler_id
INNER JOIN account_static_device asd ON lp.device_id = asd.device_id
LEFT JOIN overhaul_boiler ob ON lp.overhaul_id = ob.overhaul_id
WHERE lp.record_time >= @t_start
  AND lp.record_time < @t_end
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR lp.row_num = @row_no)
  AND (@tube_no IS NULL OR lp.pipe_num = @tube_no)

UNION ALL

SELECT
  'leakage' AS 记录类型,
  ab.boiler_name,
  asd.device_name,
  ol.row_num,
  NULL,
  ol.leakage_date,
  NULL,
  ol.leakage_descrip,
  ol.handling_method,
  ol.leakage_reason,
  NULL
FROM overhual_leakage ol
INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id
WHERE ol.leakage_date >= @t_start
  AND ol.leakage_date < @t_end
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR ol.row_num = @row_no)

UNION ALL

SELECT
  'defect_repair' AS 记录类型,
  ab.boiler_name,
  asd.device_name,
  r.row_num,
  NULL,
  r.mark_time,
  ob.overhaul_name,
  r.defect_type,
  rt.remark,
  NULL,
  IFNULL(rt.is_change, 0)
FROM overhaul_record r
INNER JOIN overhaul_record_tubes rt ON r.id = rt.overhaul_record_id
INNER JOIN account_static_device asd ON r.device_id = asd.device_id
INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id
LEFT JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id
WHERE r.del_flag = '0'
  AND r.mark_type = '2'
  AND r.mark_time >= @t_start
  AND r.mark_time < @t_end
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR CAST(IFNULL(r.row_num, '0') AS SIGNED) = @row_no)
ORDER BY 记录时间 DESC
LIMIT 300;

-- q2 补充：年平均减薄速率汇总
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面,
  tr.row_num AS 排数,
  tr.pipe_num AS 管数,
  tr.wall_thickness_rate AS 减薄速率,
  tr.residual_life AS 剩余寿命月,
  tr.last_measure_date AS 最后测厚日期
FROM overhaul_thickness_rate tr
INNER JOIN account_boiler ab ON tr.boiler_id = ab.boiler_id
INNER JOIN account_static_device asd ON tr.device_id = asd.device_id
WHERE (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR tr.row_num = @row_no)
  AND (@tube_no IS NULL OR tr.pipe_num = @tube_no)
ORDER BY tr.wall_thickness_rate DESC
LIMIT 100;

-- =============================================================================
-- q3 壁温超温数据（累计超温时长、峰值、壁温偏差）
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面,
  p.row_num AS 排数,
  p.pipe_num AS 管数,
  p.point_name AS 测点名称,
  COUNT(*) AS 超温次数,
  SUM(t.limit_duration) AS 累计超温时长_秒,
  MAX(t.highest_temp) AS 超温峰值_℃,
  MAX(t.limit_temp) AS 壁温限值_℃,
  ROUND(MAX(t.highest_temp - t.limit_temp), 2) AS 最大壁温偏差_℃,
  ROUND(AVG(t.highest_temp - t.limit_temp), 2) AS 平均壁温偏差_℃
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
INNER JOIN base_temp_point p ON t.pi_code = p.point_code
INNER JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN account_device_piperow adr ON t.device_id = adr.device_id
WHERE t.start_time >= @t_start
  AND t.start_time < @t_end
  AND t.highest_temp > t.limit_temp
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@piperow_keyword IS NULL OR @piperow_keyword = '' OR adr.piperow_name LIKE CONCAT('%', @piperow_keyword, '%'))
  AND (@row_no IS NULL OR p.row_num = @row_no)
  AND (@tube_no IS NULL OR p.pipe_num = @tube_no)
GROUP BY ab.boiler_name, asd.device_name, p.row_num, p.pipe_num, p.point_name
ORDER BY 累计超温时长_秒 DESC
LIMIT 200;

-- =============================================================================
-- q4 吹灰运行数据（频次、压力、累计吹扫时长）
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  IFNULL(asd.device_name, '未知受热面') AS 受热面名称,
  sb.blower_name AS 吹灰器名称,
  sb.blower_code AS 吹灰器编号,
  COUNT(*) AS 吹灰次数,
  ROUND(SUM(r.blowing_duration) / 60.0, 1) AS 累计吹扫时长_分钟,
  ROUND(AVG(r.blowing_duration) / 60.0, 1) AS 平均吹扫时长_分钟,
  ROUND(MAX(r.blowing_duration) / 60.0, 1) AS 最大单次吹扫_分钟,
  MAX(attr.attr_value) AS 吹扫压力属性值,
  DATE_FORMAT(MIN(r.start_time), '%Y-%m-%d %H:%i:%s') AS 首次吹灰时间,
  DATE_FORMAT(MAX(r.start_time), '%Y-%m-%d %H:%i:%s') AS 末次吹灰时间
FROM monitor_soot_blower_run_record r
INNER JOIN base_soot_blower sb ON r.blower_id = sb.blower_id
INNER JOIN account_boiler ab ON sb.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON sb.device_id = asd.device_id
LEFT JOIN base_soot_blower_attr attr ON sb.blower_id = attr.blower_id
  AND (attr.attr_name LIKE '%压力%' OR LOWER(attr.attr_name) LIKE '%pressure%')
WHERE r.start_time >= @t_start
  AND r.start_time < @t_end
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
GROUP BY ab.boiler_name, asd.device_name, sb.blower_name, sb.blower_code
ORDER BY 吹灰次数 DESC, 累计吹扫时长_分钟 DESC
LIMIT 200;

-- =============================================================================
-- q5 烟气煤质数据（烟温/烟速/飞灰浓度 — 测点 tag 按 base_dev_pi_b 描述匹配）
-- =============================================================================
SELECT
  IFNULL(pi.crew_type, '未关联机组') AS 机组类型,
  spd.tag AS 测点编码,
  IFNULL(pi.pi_name_ch, pi.original_name_ch) AS 测点描述,
  COUNT(*) AS 采样点数,
  ROUND(AVG(spd.value), 3) AS 平均值,
  ROUND(MAX(spd.value), 3) AS 最大值,
  ROUND(MIN(spd.value), 3) AS 最小值,
  DATE_FORMAT(MIN(spd.data_time), '%Y-%m-%d %H:%i:%s') AS 首样本时间,
  DATE_FORMAT(MAX(spd.data_time), '%Y-%m-%d %H:%i:%s') AS 末样本时间
FROM sis_pi_data spd
INNER JOIN base_dev_pi_b pi ON spd.tag = pi.pi_code
WHERE spd.data_time >= @t_start
  AND spd.data_time < @t_end
  AND (
    IFNULL(pi.pi_name_ch, '') LIKE '%烟温%'
    OR IFNULL(pi.pi_name_ch, '') LIKE '%烟速%'
    OR IFNULL(pi.pi_name_ch, '') LIKE '%飞灰%'
    OR IFNULL(pi.original_name_ch, '') LIKE '%烟温%'
    OR IFNULL(pi.original_name_ch, '') LIKE '%烟速%'
    OR IFNULL(pi.original_name_ch, '') LIKE '%飞灰%'
    OR IFNULL(pi.original_name_ch, '') LIKE '%浓度%'
  )
  AND (
    @unit_keyword IS NULL
    OR @unit_keyword = ''
    OR IFNULL(pi.crew_type, '') LIKE CONCAT('%', @unit_keyword, '%')
    OR IFNULL(pi.original_name_ch, '') LIKE CONCAT('%', @unit_keyword, '%')
  )
GROUP BY pi.crew_type, spd.tag, pi.pi_name_ch, pi.original_name_ch
HAVING 采样点数 >= 1
ORDER BY 采样点数 DESC
LIMIT 200;
