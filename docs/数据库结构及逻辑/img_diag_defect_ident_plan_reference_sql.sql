-- 缺陷识别看图诊断数据计划参考 SQL（analysis_plan_img_diag_defect_ident · q1 + q2a～e + q3～q5）
-- 对照：configs/prompts.yaml → analysis_plan_img_diag_defect_ident
-- 字段/表关联对齐 DBA 文档：基于AI的锅炉四管防磨防爆智能系统升级研发202606161436-数据计划.docx
-- 数据库：fmfb · TiDB/MySQL 8 兼容
-- 占位符（NL2SQL 基座从用户问题解析后改写）：
--   @unit_keyword    锅炉名称关键字（空/NULL 则不过滤锅炉）
--   @device_keyword  受热面/设备名称关键字
--   @piperow_keyword 管排名称关键字
--   @row_no          排数（NULL 则跳过排数过滤）
--   @tube_no         管数/管号（NULL 则跳过管号过滤）
--   @t_start         时间窗起点（含）
--   @t_end           时间窗终点（不含）
-- 约束：每条 plan 问句对应单条可执行 SQL；禁止 WITH/CTE

-- =============================================================================
-- q1 管段基础参数（规格材质、壁厚/胀粗/壁温限值、累计运行时长）(基础台账数据，无时间窗)
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面名称,
  adp.piperow_name AS 管排名称,
  adp.model AS 规格材质,
  adp.piperow_diameter AS 管直径,
  adp.piperow_thickness AS 设计壁厚,
  adp.row_count AS 台账排数,
  adp.pipe_count AS 台账管数,
  MAX(onc.wall_thickness_limit) AS 壁厚限值,
  MAX(onc.out_thickness_limit) AS 胀粗限值,
  MAX(btd.over_hot_limit) AS 壁温限值,
  ab.run_date AS 投产日期,
  DATEDIFF(CURDATE(), ab.run_date) AS 累计运行天数,
  RUN_WIN.统计窗内运行时长_小时,
  RUN_ALL.累计运行时长_小时
FROM account_boiler ab
INNER JOIN account_static_device asd ON ab.boiler_id = asd.boiler_id
LEFT JOIN account_device_piperow adp ON asd.device_id = adp.device_id
LEFT JOIN overhaul_new_checklocation onc ON asd.device_id = onc.device_id AND IFNULL(onc.del_flag, 0) = 0
LEFT JOIN base_temp_device btd ON asd.device_id = btd.device_id
LEFT JOIN (
  SELECT
    boiler_id,
    ROUND(SUM(TIMESTAMPDIFF(SECOND, start_date, IFNULL(stop_date, NOW()))) / 3600, 2) AS 统计窗内运行时长_小时
  FROM monitor_boiler_start_stop
  WHERE start_date >= @t_start
    AND start_date < @t_end
  GROUP BY boiler_id
) RUN_WIN ON ab.boiler_id = RUN_WIN.boiler_id
LEFT JOIN (
  SELECT
    boiler_id,
    ROUND(SUM(TIMESTAMPDIFF(SECOND, start_date, IFNULL(stop_date, NOW()))) / 3600, 2) AS 累计运行时长_小时
  FROM monitor_boiler_start_stop
  GROUP BY boiler_id
) RUN_ALL ON ab.boiler_id = RUN_ALL.boiler_id
WHERE (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@piperow_keyword IS NULL OR @piperow_keyword = '' OR adp.piperow_name LIKE CONCAT('%', @piperow_keyword, '%'))
GROUP BY
  ab.boiler_name, asd.device_name, adp.piperow_name, adp.model, adp.piperow_diameter,
  adp.piperow_thickness, adp.row_count, adp.pipe_count, ab.run_date,
  RUN_WIN.统计窗内运行时长_小时, RUN_ALL.累计运行时长_小时
ORDER BY ab.boiler_name, asd.device_name, adp.piperow_name
LIMIT 50;

-- =============================================================================
-- q2 检修处置历史（近3次壁厚、遗留问题、减薄速率、泄爆、补焊/换管）
-- =============================================================================

-- q2-a 近 3 次测厚记录（mark_type=1）（时间窗口为用户问题中解析锚点时间，结合数据计划plan中锚点说明，查询指定范围 近 3 次测厚记录）
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面名称,
  ob.overhaul_name AS 检修名称,
  ob.begin_date AS 检修时间,
  orc.row_num AS 管排号,
  ort.thickness AS 实测壁厚,
  onc.wall_thickness AS 原始壁厚,
  onc.wall_thickness_limit AS 壁厚限值,
  ort.create_time AS 测量时间
FROM overhaul_boiler ob
INNER JOIN overhaul_record orc ON ob.overhaul_id = orc.overhaul_id
INNER JOIN overhaul_record_tubes ort ON orc.id = ort.overhaul_record_id
LEFT JOIN overhaul_new_checklocation onc ON orc.check_id = onc.id
INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id
INNER JOIN account_static_device asd ON orc.device_id = asd.device_id
WHERE orc.del_flag = '0'
  AND orc.mark_type = '1'
	AND (@t_start IS NULL OR @t_start = '' OR ort.create_time >= @t_start)
  AND (@t_end IS NULL OR @t_end = '' OR ort.create_time < @t_end)
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR CAST(IFNULL(orc.row_num, '0') AS SIGNED) = @row_no)
ORDER BY ort.create_time DESC
LIMIT 3;

-- q2-b 年平均减薄速率(无时间窗口，查询指定范围的 年平均减薄速率)
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面名称,
  ob.overhaul_name AS 检修名称,
  onc.name AS 检测位置名称,
  otr.row_num AS 管排号,
  otr.pipe_num AS 管编号,
  otr.wall_thickness AS 原始壁厚,
  otr.wall_thickness_measure AS 实测壁厚,
  otr.wall_thickness_rate AS 年平均减薄速率,
  otr.out_thickness_rate AS 年胀粗速率,
  otr.residual_life AS 预估剩余寿命_月,
  otr.last_measure_date AS 末次测量时间
FROM overhaul_boiler ob
INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id
INNER JOIN overhaul_thickness_rate otr ON ob.overhaul_id = otr.overhaul_id
LEFT JOIN overhaul_new_checklocation onc ON otr.location_id = onc.id
LEFT JOIN account_static_device asd ON otr.device_id = asd.device_id
WHERE (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR otr.row_num = @row_no)
  AND (@tube_no IS NULL OR otr.pipe_num = @tube_no)
ORDER BY otr.row_num, otr.pipe_num
LIMIT 50;

-- q2-c 泄爆/泄漏记录(无时间窗口，查询指定机组受热面管排的最近50次泄爆记录)
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面名称,
  ob.overhaul_name AS 对应检修名称,
  ol.leakage_date AS 泄漏时间,
  ol.leakage_descrip AS 泄漏位置,
  ol.row_num AS 管排号,
  ol.pipe_num AS 管编号,
  ol.leakage_reason AS 泄爆原因,
  ol.handling_method AS 处置方式,
  ol.is_abnormal_stop AS 是否造成非停
FROM overhual_leakage ol
LEFT JOIN overhaul_boiler ob ON ol.overhaul_id = ob.overhaul_id
INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id
WHERE
  (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR ol.row_num = @row_no)
  AND (@tube_no IS NULL OR ol.pipe_num = @tube_no)
ORDER BY ol.leakage_date DESC
LIMIT 50;

-- q2-d 遗留问题及处置结果(时间窗口为用户问题中解析锚点时间，结合数据计划plan中锚点说明，查询指定管排最近50条遗留问题及处置结果)
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面名称,
  ob.overhaul_name AS 检修名称,
  lp.row_num AS 管排号,
  lp.pipe_num AS 管编号,
  lp.problem_descrip AS 问题描述,
  lp.deal_content AS 处置内容,
  lp.status AS 处理状态,
  lp.record_time AS 记录时间
FROM overhaul_legacy_problem lp
INNER JOIN account_boiler ab ON lp.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON lp.device_id = asd.device_id
LEFT JOIN overhaul_boiler ob ON lp.overhaul_id = ob.overhaul_id
WHERE
	(@t_start IS NULL OR @t_start = '' OR lp.record_time >= @t_start)
  AND (@t_end IS NULL OR @t_end = '' OR lp.record_time < @t_end)
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR lp.row_num = @row_no)
  AND (@tube_no IS NULL OR lp.pipe_num = @tube_no)
ORDER BY lp.record_time DESC
LIMIT 50;

-- q2-e 补焊/换管记录(时间窗口为用户问题中解析锚点时间，结合数据计划plan中锚点说明，查询指定管排最近50条补焊/换管记录)
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面名称,
  ob.overhaul_name AS 检修名称,
  onc.name AS 检测位置名称,
  ort.tube_position AS 管道位置描述,
  orc.row_num AS 排数,
  ort.thickness AS 缺陷位置壁厚,
  ort.is_change AS 是否换管,
  CASE
    WHEN ort.is_change = 1 THEN '整体换管'
    WHEN orc.mark_type = '2' AND IFNULL(ort.is_change, 0) = 0 THEN '缺陷补焊'
    ELSE '无处置'
  END AS 处置类型,
  orc.defect_type AS 缺陷类型,
  ort.create_time AS 处置时间
FROM overhaul_boiler ob
INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id
INNER JOIN overhaul_record orc ON ob.overhaul_id = orc.overhaul_id
INNER JOIN overhaul_record_tubes ort ON orc.id = ort.overhaul_record_id
LEFT JOIN overhaul_new_checklocation onc ON orc.check_id = onc.id
INNER JOIN account_static_device asd ON orc.device_id = asd.device_id
WHERE orc.del_flag = '0'
  AND (ort.is_change = 1 OR orc.mark_type = '2')
	AND (@t_start IS NULL OR @t_start = '' OR ort.create_time >= @t_start)
  AND (@t_end IS NULL OR @t_end = '' OR ort.create_time < @t_end)
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR CAST(IFNULL(orc.row_num, '0') AS SIGNED) = @row_no)
ORDER BY ort.create_time DESC
LIMIT 50;

-- =============================================================================
-- q3 壁温超温数据（时间窗口为用户问题中解析锚点时间，结合数据计划plan中锚点说明，查询指定范围的近三天超温数据）
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  asd.device_name AS 受热面名称,
  mht.pi_code AS 测点编码,
  IFNULL(btp.point_name, mht.pi_code) AS 测点名称,
  btp.row_num AS 排数,
  btp.pipe_num AS 管数,
  mht.limit_temp AS 设计壁温限值_℃,
  MAX(mht.highest_temp) AS 超温峰值_℃,
  ROUND(MAX(mht.highest_temp) - mht.limit_temp, 2) AS 最大壁温偏差_℃,
  ROUND(SUM(mht.limit_duration) / 3600, 2) AS 累计超温时长_小时,
  COUNT(*) AS 超温事件次数
FROM monitor_hotarea_temp mht
INNER JOIN account_boiler ab ON mht.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON mht.device_id = asd.device_id
LEFT JOIN base_temp_point btp ON mht.pi_code = btp.point_code
WHERE
	(@t_start IS NULL OR @t_start = '' OR mht.start_time >= @t_start)
  AND (@t_end IS NULL OR @t_end = '' OR mht.start_time < @t_end)
  AND mht.highest_temp > mht.limit_temp
  AND (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
  AND (@row_no IS NULL OR btp.row_num = @row_no)
  AND (@tube_no IS NULL OR btp.pipe_num = @tube_no)
GROUP BY
  ab.boiler_name, asd.device_name, mht.pi_code, btp.point_name, btp.row_num, btp.pipe_num, mht.limit_temp
ORDER BY 累计超温时长_小时 DESC
LIMIT 50;

-- =============================================================================
-- q4 吹灰运行数据（时间窗口为用户问题中解析锚点时间，结合数据计划plan中锚点说明，查询指定范围的近三天吹灰运行数据）
-- =============================================================================
SELECT
  ab.boiler_name AS 锅炉名称,
  IFNULL(asd.device_name, '未知受热面') AS 受热面名称,
  bsb.blower_name AS 吹灰器名称,
  bsb.blower_code AS 吹灰器编号,
  COUNT(msrr.id) AS 吹灰频次,
  ROUND(SUM(msrr.blowing_duration) / 3600, 2) AS 累计吹扫时长_小时,
  ROUND(AVG(msrr.blowing_duration) / 60, 1) AS 平均单次吹扫_分钟,
  '无采集数据' AS 吹扫压力
FROM base_soot_blower bsb
LEFT JOIN monitor_soot_blower_run_record msrr
  ON bsb.blower_id = msrr.blower_id
	AND (@t_start IS NULL OR @t_start = '' OR msrr.start_time >= @t_start)
  AND (@t_end IS NULL OR @t_end = '' OR msrr.start_time < @t_end)
INNER JOIN account_boiler ab ON bsb.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON bsb.device_id = asd.device_id
WHERE (@unit_keyword IS NULL OR @unit_keyword = '' OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))
  AND (@device_keyword IS NULL OR @device_keyword = '' OR asd.device_name LIKE CONCAT('%', @device_keyword, '%'))
GROUP BY ab.boiler_name, asd.device_name, bsb.blower_name, bsb.blower_code
ORDER BY 吹灰频次 DESC, 累计吹扫时长_小时 DESC
LIMIT 50;

-- =============================================================================
-- q5 烟气煤质数据（时间窗口为用户问题中解析锚点时间，结合数据计划plan中锚点说明，查询指定范围的近三天烟气煤质数据）
-- =============================================================================
SELECT
  bpi.pi_code AS 测点编码,
  bpi.pi_name_ch AS 测点名称,
  COUNT(*) AS 采样点数,
  ROUND(AVG(CAST(spd.value AS DECIMAL(12, 4))), 3) AS 平均值,
  ROUND(MAX(CAST(spd.value AS DECIMAL(12, 4))), 3) AS 最大值,
  ROUND(MIN(CAST(spd.value AS DECIMAL(12, 4))), 3) AS 最小值,
  DATE_FORMAT(MIN(spd.data_time), '%Y-%m-%d %H:%i:%s') AS 首样本时间,
  DATE_FORMAT(MAX(spd.data_time), '%Y-%m-%d %H:%i:%s') AS 末样本时间
FROM sis_pi_data spd
INNER JOIN base_dev_pi_b bpi ON spd.tag = bpi.pi_code
WHERE
	(@t_start IS NULL OR @t_start = '' OR spd.data_time >= @t_start)
  AND (@t_end IS NULL OR @t_end = '' OR spd.data_time < @t_end)
  AND (
    bpi.pi_name_ch IN ('烟气温度', '烟速', '飞灰浓度', '吹扫压力')
    OR IFNULL(bpi.pi_name_ch, '') LIKE '%烟温%'
    OR IFNULL(bpi.pi_name_ch, '') LIKE '%烟速%'
    OR IFNULL(bpi.pi_name_ch, '') LIKE '%飞灰%'
    OR IFNULL(bpi.original_name_ch, '') LIKE '%烟温%'
    OR IFNULL(bpi.original_name_ch, '') LIKE '%烟速%'
    OR IFNULL(bpi.original_name_ch, '') LIKE '%飞灰%'
    OR IFNULL(bpi.original_name_ch, '') LIKE '%浓度%'
  )
  AND (
    @unit_keyword IS NULL
    OR @unit_keyword = ''
    OR IFNULL(bpi.crew_type, '') LIKE CONCAT('%', @unit_keyword, '%')
    OR IFNULL(bpi.original_name_ch, '') LIKE CONCAT('%', @unit_keyword, '%')
  )
GROUP BY bpi.pi_code, bpi.pi_name_ch
HAVING 采样点数 >= 1
ORDER BY 末样本时间 DESC
LIMIT 50;