1. [超温-HAVING] 近30天超温次数不少于5次的测点
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称,
       IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码,
       COUNT(*) AS 超温次数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃,
       ROUND(MAX(t.limit_duration) / 60, 1) AS 最大超温时长_分钟
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id)
WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) AND t.start_time < CURDATE()
  AND t.highest_temp > t.limit_temp
GROUP BY ab.boiler_name, asd.device_name, btp.point_name, t.pi_code
HAVING COUNT(*) >= 5
ORDER BY 超温次数 DESC, 最大超温差值_℃ DESC
2. [超温-环比] 1号锅炉本月与上月超温次数及最大差值对比
SELECT ab.boiler_name AS 机组名称,
       SUM(CASE WHEN t.start_time >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
                 AND t.start_time < CURDATE() + INTERVAL 1 DAY THEN 1 ELSE 0 END) AS 本月超温次数,
       SUM(CASE WHEN t.start_time >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
                 AND t.start_time < DATE_FORMAT(CURDATE(), '%Y-%m-01') THEN 1 ELSE 0 END) AS 上月超温次数,
       MAX(CASE WHEN t.start_time >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
                 AND t.start_time < CURDATE() + INTERVAL 1 DAY
                THEN t.highest_temp - t.limit_temp END) AS 本月最大超温差值_℃,
       MAX(CASE WHEN t.start_time >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
                 AND t.start_time < DATE_FORMAT(CURDATE(), '%Y-%m-01')
                THEN t.highest_temp - t.limit_temp END) AS 上月最大超温差值_℃
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
WHERE ab.boiler_name LIKE '%1号锅炉%'
  AND t.start_time >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01')
  AND t.start_time < CURDATE() + INTERVAL 1 DAY
  AND t.highest_temp > t.limit_temp
GROUP BY ab.boiler_name
3. [超温-负荷分段] 近7天各机组按负荷率分段统计超温次数
SELECT ab.boiler_name AS 机组名称,
       SUM(CASE WHEN t.mw_value / NULLIF(ab.edfh, 0) < 0.6 THEN 1 ELSE 0 END) AS 负荷率低于60pct次数,
       SUM(CASE WHEN t.mw_value / NULLIF(ab.edfh, 0) >= 0.6 AND t.mw_value / NULLIF(ab.edfh, 0) < 0.9 THEN 1 ELSE 0 END) AS 负荷率60到90pct次数,
       SUM(CASE WHEN t.mw_value / NULLIF(ab.edfh, 0) >= 0.9 THEN 1 ELSE 0 END) AS 负荷率不低于90pct次数,
       COUNT(*) AS 超温总次数
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE()
  AND t.highest_temp > t.limit_temp AND t.mw_value IS NOT NULL
GROUP BY ab.boiler_name
ORDER BY ab.boiler_name
4. [超温-占比] 本月各受热面超温次数及占本机组比例
SELECT x.机组名称, x.受热面名称, x.超温次数,
       ROUND(x.超温次数 * 100.0 / NULLIF(y.机组超温总次数, 0), 2) AS 占本机组比例_percent
FROM (
  SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ab.boiler_id, COUNT(*) AS 超温次数
  FROM monitor_hotarea_temp t
  INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
  LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
  WHERE t.start_time >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
    AND t.start_time < CURDATE() + INTERVAL 1 DAY
    AND t.highest_temp > t.limit_temp
  GROUP BY ab.boiler_id, ab.boiler_name, asd.device_name
) x
INNER JOIN (
  SELECT t.boiler_id, COUNT(*) AS 机组超温总次数
  FROM monitor_hotarea_temp t
  WHERE t.start_time >= DATE_FORMAT(CURDATE(), '%Y-%m-01')
    AND t.start_time < CURDATE() + INTERVAL 1 DAY
    AND t.highest_temp > t.limit_temp
  GROUP BY t.boiler_id
) y ON x.boiler_id = y.boiler_id
ORDER BY x.机组名称, 占本机组比例_percent DESC
5. [超温-连续日] 近14天同一测点超温天数不少于3天的清单
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称,
       IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码,
       COUNT(DISTINCT DATE(t.start_time)) AS 超温天数,
       COUNT(*) AS 超温次数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃
FROM monitor_hotarea_temp t
INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON t.device_id = asd.device_id
LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id)
WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 14 DAY) AND t.start_time < CURDATE()
  AND t.highest_temp > t.limit_temp
GROUP BY ab.boiler_name, asd.device_name, btp.point_name, t.pi_code
HAVING COUNT(DISTINCT DATE(t.start_time)) >= 3
ORDER BY 超温天数 DESC, 超温次数 DESC
6. [检修-换管率] 各次检修换管数占缺陷数比例（换管率）
SELECT ab.boiler_name AS 机组名称, ob.overhaul_name AS 检修名称, ob.overhaul_year AS 检修年份,
       ob.defect_num AS 缺陷数, ob.tubchage_num AS 换管数, ob.legacy_defect_num AS 遗留缺陷数,
       ROUND(IFNULL(ob.tubchage_num, 0) * 100.0 / NULLIF(ob.defect_num, 0), 2) AS 换管率_percent
FROM overhaul_boiler ob
INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id
WHERE IFNULL(ob.defect_num, 0) > 0
ORDER BY 换管率_percent DESC, ob.overhaul_year DESC
7. [跨域-超温+缺陷] 近90天高频超温且历史上有磨损缺陷的受热面
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称,
       ht.近90天超温次数, ht.最大超温差值_℃, df.磨损缺陷条数
FROM (
  SELECT t.boiler_id, t.device_id, COUNT(*) AS 近90天超温次数,
         MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃
  FROM monitor_hotarea_temp t
  WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 90 DAY) AND t.start_time < CURDATE()
    AND t.highest_temp > t.limit_temp AND t.device_id IS NOT NULL
  GROUP BY t.boiler_id, t.device_id
  HAVING COUNT(*) >= 10
) ht
INNER JOIN account_boiler ab ON ht.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON ht.device_id = asd.device_id
INNER JOIN (
  SELECT ob.boiler_id, r.device_id, COUNT(*) AS 磨损缺陷条数
  FROM overhaul_record r
  INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id
  WHERE r.mark_type = '2' AND r.defect_type = '2' AND IFNULL(r.del_flag, '0') = '0'
    AND r.device_id IS NOT NULL
  GROUP BY ob.boiler_id, r.device_id
) df ON ht.boiler_id = df.boiler_id AND ht.device_id = df.device_id
ORDER BY ht.近90天超温次数 DESC
8. [检修-复合] 最近一次检修中既测厚减薄又标记换管的管子
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称,
       rt.tube_code AS 管位置编号, rt.tube_position AS 管位置描述,
       rt.thickness AS 管子壁厚, rt.length AS 长度, rt.is_change AS 是否换管,
       otr.reduce_rate AS 减薄速率
FROM overhaul_record_tubes rt
INNER JOIN overhaul_record r ON rt.overhaul_record_id = r.id
INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id
INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON r.device_id = asd.device_id
LEFT JOIN overhaul_thickness_rate otr ON otr.overhaul_id = ob.overhaul_id
  AND otr.device_id = r.device_id
WHERE ab.boiler_name LIKE '%1号锅炉%'
  AND rt.is_change = 1
  AND IFNULL(r.del_flag, '0') = '0'
  AND IFNULL(otr.reduce_rate, 0) > 0
  AND ob.begin_date = (
    SELECT MAX(ob2.begin_date)
    FROM overhaul_boiler ob2
    INNER JOIN account_boiler ab2 ON ob2.boiler_id = ab2.boiler_id
    WHERE ab2.boiler_name LIKE '%1号锅炉%'
  )
ORDER BY otr.reduce_rate DESC, rt.tube_code
说明：若现场 overhaul_thickness_rate 与管子粒度关联键不同，可按现场字典改 JOIN 条件；逻辑仍是「最近一次检修 × 换管 × 减薄」。

9. [泄爆-关联超温] 近三年非停泄爆前30天同受热面是否有差值≥20℃超温
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称,
       ol.leakage_date AS 泄漏时间, ol.leakage_reason AS 泄漏原因, ol.is_abnormal_stop AS 是否非停,
       COUNT(t.id) AS 泄爆前30天严重超温次数,
       MAX(t.highest_temp - t.limit_temp) AS 泄爆前最大超温差值_℃
FROM overhual_leakage ol
INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id
LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id
LEFT JOIN monitor_hotarea_temp t
  ON t.boiler_id = ol.boiler_id
 AND (ol.device_id IS NULL OR t.device_id = ol.device_id)
 AND t.start_time >= DATE_SUB(ol.leakage_date, INTERVAL 30 DAY)
 AND t.start_time < ol.leakage_date
 AND t.highest_temp > t.limit_temp
 AND (t.highest_temp - t.limit_temp) >= 20
WHERE IFNULL(ol.is_abnormal_stop, 0) = 1
  AND ol.leakage_date >= DATE_SUB(CURDATE(), INTERVAL 3 YEAR)
GROUP BY ab.boiler_name, asd.device_name, ol.leakage_date, ol.leakage_reason, ol.is_abnormal_stop
ORDER BY ol.leakage_date DESC
若 monitor_hotarea_temp 主键不是 id，把 COUNT(t.id) 改为 COUNT(*) 即可。

10. [吹灰×超温] 近7天吹灰工作电流偏高当天、同机组是否有超温
SELECT ab.boiler_name AS 机组名称, d.吹灰异常日期,
       d.吹灰异常记录数, IFNULL(h.当日超温次数, 0) AS 当日超温次数,
       IFNULL(h.最大超温差值_℃, 0) AS 当日最大超温差值_℃
FROM (
  SELECT cm.boiler_id, DATE(rr.record_time) AS 吹灰异常日期, COUNT(*) AS 吹灰异常记录数
  FROM monitor_soot_blower_run_record rr
  INNER JOIN base_soot_blower sb ON rr.blower_id = sb.blower_id
  INNER JOIN account_boiler ab0 ON sb.boiler_id = ab0.boiler_id
  WHERE rr.record_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND rr.record_time < CURDATE()
    AND rr.work_current IS NOT NULL
  GROUP BY cm.boiler_id, DATE(rr.record_time)
) d
INNER JOIN account_boiler ab ON d.boiler_id = ab.boiler_id
LEFT JOIN (
  SELECT t.boiler_id, DATE(t.start_time) AS 超温日期, COUNT(*) AS 当日超温次数,
         MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃
  FROM monitor_hotarea_temp t
  WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE()
    AND t.highest_temp > t.limit_temp
  GROUP BY t.boiler_id, DATE(t.start_time)
) h ON d.boiler_id = h.boiler_id AND d.吹灰异常日期 = h.超温日期
ORDER BY d.吹灰异常日期 DESC, 当日超温次数 DESC
说明：现有第 88 条是「电流异常偏高明细」；本条改为按日交叉超温。若现场吹灰异常判定字段不是 work_current 阈值，可套用你们第 88 条的同一过滤条件替换子查询 d。