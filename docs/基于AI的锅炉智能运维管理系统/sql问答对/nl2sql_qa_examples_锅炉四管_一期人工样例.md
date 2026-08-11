# NL2SQL 问答样例（锅炉四管 · 一期人工）

> **用途**：摄入 RAG 命名空间 `nl2sql_qa_examples`，提升智能客服 / 综合分析 NL2SQL 生成准确率。  
> **依据**：`docs/综合分析-超温分析 报告模板及查询数据逻辑/ai大模型数据库结构模板0331.docx` + 锅炉四管（水冷壁/过热器/再热器/省煤器）业务问法。  
> **方言**：TiDB / MySQL 8；机组过滤统一 `account_boiler.boiler_name LIKE '%N号锅炉%'`（口语「N号炉/机组」在问法中保留，SQL 侧写「N号锅炉」）。  
> **条数**：共 **100** 条。

## 一、摄入说明

1. 推荐将每条样例的 `text` 字段（或 `.texts.json` 数组元素）作为独立 chunk 摄入：
   - `namespace` = `nl2sql_qa_examples`
   - 接口：`POST /rag/ingest/texts` 或批量 ingest（见 `app/api/rag_admin.py`）
2. 正文格式与系统自动 QA 对齐，便于 `parse_sql_from_nl2sql_qa_text` 解析：
   - `【用户问题】…`
   - `【校验通过的 SQL】…`
3. 本批为**人工样例、无数据源指纹**；若开启 `NL2SQL_QA_FILTER_ENABLED`，需保证 `NL2SQL_QA_INCLUDE_LEGACY_UNSCOPED=true`（默认一般允许未打指纹的人工 QA），否则可能被过滤。
4. 入库前建议在目标库抽检 EXPLAIN / 试跑；现场表名白名单以 `ANALYSIS_NL2SQL_TABLE_SCOPE` / 部署配置为准。
5. 同目录文件：
   - `nl2sql_qa_examples_锅炉四管_一期人工样例.jsonl`：逐条 JSON（含 question/sql/text）
   - `nl2sql_qa_examples_锅炉四管_一期人工样例.texts.json`：仅 `text` 数组，便于直接 POST

## 一附、校验结论（生成后静态复核）

- 已对照 `ai大模型数据库结构模板0331` 表/字段做静态核验：**无未知表/未知字段、括号与引号配平通过**。
- 已通过项目内 `SQLValidator.validate`（只读 SELECT、无违禁写操作）。
- 已通过 sqlparse 解析；GROUP BY 与非聚合列对齐启发式检查通过。
- 缺陷类样例统一带 `mark_type='2'`；测点名关联优先 `pi_code + device_id`。
- **说明**：本环境未连业务库做 EXPLAIN/实跑；空结果可能来自现场无对应数据，不等于 SQL 错误。  样例中的测点编码（如 `10HAD11CT109`）、吹灰电流 TopN 等为模式示例，需按现场数据理解。

## 二、分类统计

| 大类 | 条数 |
|------|------|
| 台账 | 23 |
| 启停 | 2 |
| 吹灰 | 4 |
| 检修 | 21 |
| 泄爆 | 6 |
| 磨煤机 | 3 |
| 超温 | 40 |
| 防磨防爆 | 1 |

## 三、样例清单

### 1. [台账-锅炉] 查询所有锅炉的基本信息

```sql
SELECT ab.boiler_name AS 机组名称, ab.boiler_code AS 锅炉编号, ab.boiler_type AS 锅炉类型, ab.boiler_model AS 型号, ab.capacity AS 容量, ab.edfh AS 额定负荷_MW, ab.run_date AS 投产日期, ab.producer AS 生产厂家 FROM account_boiler ab ORDER BY ab.sort_by, ab.boiler_name
```

### 2. [台账-锅炉] 1号锅炉的额定负荷和投产日期是多少

```sql
SELECT ab.boiler_name AS 机组名称, ab.edfh AS 额定负荷_MW, ab.edzfl AS 额定蒸发量, ab.run_date AS 投产日期, ab.boiler_model AS 型号, ab.structure AS 布置结构 FROM account_boiler ab WHERE ab.boiler_name LIKE '%1号锅炉%'
```

### 3. [台账-锅炉] 2号机组锅炉型号和生产厂家

```sql
SELECT ab.boiler_name AS 机组名称, ab.boiler_model AS 型号, ab.producer AS 生产厂家, ab.capacity AS 容量, ab.fire_type AS 点火方式 FROM account_boiler ab WHERE ab.boiler_name LIKE '%2号锅炉%'
```

### 4. [台账-受热面] 查询1号锅炉有哪些受热面设备

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, asd.device_code AS 设备编码, asd.device_type AS 设备类型, asd.device_level AS 设备级别 FROM account_static_device asd INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' ORDER BY asd.sort_by, asd.device_name
```

### 5. [台账-受热面] 列出全厂水冷壁受热面台账

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, asd.device_code AS 设备编码, asd.device_type AS 设备类型 FROM account_static_device asd INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE asd.device_name LIKE '%水冷壁%' ORDER BY ab.boiler_name, asd.device_name
```

### 6. [台账-受热面] 查询1号锅炉过热器设备有哪些

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, asd.device_code AS 设备编码, asd.device_type AS 设备类型 FROM account_static_device asd INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND asd.device_name LIKE '%过热器%' ORDER BY asd.sort_by, asd.device_name
```

### 7. [台账-受热面] 2号锅炉再热器受热面有哪些

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, asd.device_code AS 设备编码 FROM account_static_device asd INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%2号锅炉%' AND asd.device_name LIKE '%再热器%' ORDER BY asd.device_name
```

### 8. [台账-受热面] 查询省煤器设备台账

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, asd.device_code AS 设备编码, asd.device_type AS 设备类型 FROM account_static_device asd INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE asd.device_name LIKE '%省煤器%' ORDER BY ab.boiler_name, asd.device_name
```

### 9. [台账-受热面] 1号锅炉螺旋管水冷壁的设备编码是什么

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, asd.device_code AS 设备编码, asd.device_descrip AS 设备描述 FROM account_static_device asd INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND (asd.device_name LIKE '%螺旋管%' OR asd.device_name LIKE '%水冷壁%') ORDER BY asd.device_name
```

### 10. [台账-管排] 查询1号锅炉水冷壁管排规格材质

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, adp.piperow_name AS 管排名称, adp.model AS 规格材质, adp.row_count AS 排数, adp.pipe_count AS 管数, adp.piperow_diameter AS 管直径, adp.piperow_thickness AS 管厚度 FROM account_device_piperow adp INNER JOIN account_static_device asd ON adp.device_id = asd.device_id INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND asd.device_name LIKE '%水冷壁%' ORDER BY adp.sort_by, adp.piperow_name
```

### 11. [台账-管排] 高温过热器管排的管数和材质

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, adp.piperow_name AS 管排名称, adp.model AS 规格材质, adp.pipe_count AS 管数, adp.row_count AS 排数, adp.area AS 受热面面积 FROM account_device_piperow adp INNER JOIN account_static_device asd ON adp.device_id = asd.device_id INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE asd.device_name LIKE '%高温过热器%' OR asd.device_name LIKE '%高过%' ORDER BY ab.boiler_name, adp.piperow_name
```

### 12. [台账-集箱] 查询1号锅炉过热器集箱设计压力和温度

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, adb.pipebox_name AS 集箱名称, adb.design_pressure AS 设计压力, adb.design_temp AS 设计温度, adb.work_pressure AS 工作压力, adb.model AS 规格材质, adb.pipebox_type AS 集箱类型 FROM account_device_pipebox adb INNER JOIN account_static_device asd ON adb.device_id = asd.device_id INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND asd.device_name LIKE '%过热器%' ORDER BY adb.sort_by, adb.pipebox_name
```

### 13. [台账-焊口] 查询2号锅炉水冷壁焊口信息

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, adw.weld_name AS 焊口名称, adw.weld_count AS 焊口数量, adw.weld_type AS 焊口类型, adw.weld_model_front AS 前端材质, adw.weld_model_end AS 后端材质, adw.weld_location AS 焊口位置 FROM account_device_weld adw INNER JOIN account_static_device asd ON adw.device_id = asd.device_id INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%2号锅炉%' AND asd.device_name LIKE '%水冷壁%' ORDER BY adw.sort_by, adw.weld_name
```

### 14. [台账-壁温限值] 1号锅炉各受热面壁温限值是多少

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, btd.over_hot_limit AS 壁温限值_℃, btd.pipe_count AS 总管数, btd.row_count AS 总排数 FROM base_temp_device btd INNER JOIN account_static_device asd ON btd.device_id = asd.device_id INNER JOIN account_boiler ab ON btd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' ORDER BY asd.device_name
```

### 15. [台账-测点配置] 查询1号锅炉螺旋管前墙出口壁温测点配置

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, btp.point_code AS 测点编码, btp.point_name AS 测点名称, btp.row_num AS 排数, btp.pipe_num AS 管数 FROM base_temp_point btp INNER JOIN account_static_device asd ON btp.device_id = asd.device_id INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND (btp.point_name LIKE '%螺旋管%' OR btp.point_name LIKE '%前墙出口%') ORDER BY btp.point_code
```

### 16. [超温-明细] 查询昨日锅炉超温的测点数据

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, btp.point_name AS 测点名称, t.pi_code AS 测点编码, t.start_time AS 超温开始时间, t.end_time AS 超温结束时间, t.limit_duration AS 超温时长_秒, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, t.mw_value AS 负荷_MW, t.steam_pressure_value AS 主汽压力_MPa FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY ab.boiler_name, asd.device_name, t.start_time
```

### 17. [超温-明细] 查询昨天1号锅炉超温记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码, t.start_time AS 超温开始时间, t.end_time AS 超温结束时间, t.limit_duration AS 超温时长_秒, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃, t.mw_value AS 负荷_MW FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE ab.boiler_name LIKE '%1号锅炉%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY t.highest_temp - t.limit_temp DESC, t.start_time
```

### 18. [超温-明细] 近7天2号锅炉水冷壁超温明细

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码, t.start_time AS 超温开始时间, t.end_time AS 超温结束时间, t.limit_duration AS 超温时长_秒, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE ab.boiler_name LIKE '%2号锅炉%' AND asd.device_name LIKE '%水冷壁%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY t.start_time DESC
```

### 19. [超温-明细] 本月1号锅炉过热器超温有哪些测点

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码, COUNT(*) AS 超温次数, MAX(t.highest_temp) AS 最高壁温_℃, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃, MAX(t.limit_duration) AS 最大超温时长_秒 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE ab.boiler_name LIKE '%1号锅炉%' AND asd.device_name LIKE '%过热器%' AND t.start_time >= DATE_FORMAT(CURDATE(), '%Y-%m-01') AND t.start_time < CURDATE() + INTERVAL 1 DAY AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name, asd.device_name, btp.point_name, t.pi_code ORDER BY 最大超温差值_℃ DESC
```

### 20. [超温-明细] 近30天再热器超温记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃, t.limit_duration AS 超温时长_秒, t.mw_value AS 负荷_MW FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE asd.device_name LIKE '%再热器%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY t.start_time DESC
```

### 21. [超温-明细] 昨天省煤器有没有超温

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, t.limit_duration AS 超温时长_秒 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE asd.device_name LIKE '%省煤器%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY ab.boiler_name, t.start_time
```

### 22. [超温-统计] 昨日各机组超温次数统计

```sql
SELECT ab.boiler_name AS 机组名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp) AS 最高壁温_℃, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃, ROUND(MAX(t.limit_duration) / 60, 0) AS 最大连续超温时长_分钟 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name ORDER BY 超温次数 DESC
```

### 23. [超温-统计] 近7天1号锅炉各受热面超温次数

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp) AS 最高壁温_℃, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name, asd.device_name ORDER BY 超温次数 DESC
```

### 24. [超温-统计] 本月全厂四管超温按受热面汇总

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃, ROUND(MAX(t.limit_duration) / 60, 0) AS 最大连续超温时长_分钟 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id WHERE t.start_time >= DATE_FORMAT(CURDATE(), '%Y-%m-01') AND t.start_time < CURDATE() + INTERVAL 1 DAY AND t.highest_temp > t.limit_temp AND (asd.device_name LIKE '%水冷壁%' OR asd.device_name LIKE '%过热器%' OR asd.device_name LIKE '%再热器%' OR asd.device_name LIKE '%省煤器%') GROUP BY ab.boiler_name, asd.device_name ORDER BY ab.boiler_name, 超温次数 DESC
```

### 25. [超温-严重] 近7天超温差值大于等于20度的严重超温记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃, t.limit_duration AS 超温时长_秒, t.mw_value AS 负荷_MW FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp AND (t.highest_temp - t.limit_temp) >= 20 ORDER BY 超温差值_℃ DESC, t.start_time DESC
```

### 26. [超温-严重] 昨天超温时长超过30分钟的测点

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.end_time AS 超温结束时间, ROUND(t.limit_duration / 60, 1) AS 超温时长_分钟, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp AND t.limit_duration >= 1800 ORDER BY t.limit_duration DESC
```

### 27. [超温-工况] 昨日1号锅炉超温时的平均负荷和主汽压力

```sql
SELECT ab.boiler_name AS 机组名称, ROUND(AVG(t.mw_value), 2) AS 平均负荷_MW, ROUND(AVG(t.mw_value) / NULLIF(MAX(ab.edfh), 0) * 100, 2) AS 负荷率_percent, ROUND(AVG(t.steam_pressure_value), 2) AS 平均主汽压力_MPa, COUNT(*) AS 超温记录数 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_id, ab.boiler_name
```

### 28. [超温-趋势] 近7天1号锅炉按日统计超温测点数

```sql
SELECT ab.boiler_name AS 机组名称, DATE(t.start_time) AS 超温日期, COUNT(DISTINCT t.pi_code) AS 当日超温测点数, COUNT(*) AS 超温次数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name, DATE(t.start_time) ORDER BY 超温日期
```

### 29. [超温-Top] 近7天超温最严重的前20个测点

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码, MAX(t.highest_temp) AS 最高壁温_℃, MAX(t.limit_temp) AS 限值_℃, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃, COUNT(*) AS 超温次数 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name, asd.device_name, btp.point_name, t.pi_code ORDER BY 最大超温差值_℃ DESC LIMIT 20
```

### 30. [超温-时间包络] 昨天各机组最早和最晚超温时间

```sql
SELECT ab.boiler_name AS 机组名称, DATE_FORMAT(MIN(t.start_time), '%Y-%m-%d %H:%i:%s') AS 最早超温开始时间, DATE_FORMAT(MAX(IFNULL(t.end_time, t.start_time)), '%Y-%m-%d %H:%i:%s') AS 最晚超温结束时间, COUNT(*) AS 超温次数 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name ORDER BY ab.boiler_name
```

### 31. [超温-屏式过热器] 近7天屏式过热器超温明细

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃, t.mw_value AS 负荷_MW FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE (asd.device_name LIKE '%屏式过热器%' OR asd.device_name LIKE '%屏过%') AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY t.start_time DESC
```

### 32. [超温-末级过热器] 本月末级过热器超温统计

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp) AS 最高壁温_℃, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id WHERE (asd.device_name LIKE '%末级过热器%' OR asd.device_name LIKE '%末过%') AND t.start_time >= DATE_FORMAT(CURDATE(), '%Y-%m-01') AND t.start_time < CURDATE() + INTERVAL 1 DAY AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name, asd.device_name ORDER BY 超温次数 DESC
```

### 33. [超温-低温再热器] 近30天低温再热器超温记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, t.limit_duration AS 超温时长_秒 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE (asd.device_name LIKE '%低温再热器%' OR asd.device_name LIKE '%低再%') AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY t.start_time DESC
```

### 34. [超温-高温再热器] 昨天高温再热器超温了吗

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE (asd.device_name LIKE '%高温再热器%' OR asd.device_name LIKE '%高再%') AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY 超温差值_℃ DESC
```

### 35. [检修-策划] 查询1号锅炉历次检修策划

```sql
SELECT ab.boiler_name AS 机组名称, ob.overhaul_name AS 检修名称, ob.overhaul_level AS 检修等级, ob.overhaul_year AS 检修年份, ob.begin_date AS 开始日期, ob.end_date AS 结束日期, ob.status AS 检修状态, ob.defect_num AS 缺陷数, ob.tubchage_num AS 换管数, ob.legacy_defect_num AS 遗留缺陷数 FROM overhaul_boiler ob INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' ORDER BY ob.overhaul_year DESC, ob.begin_date DESC
```

### 36. [检修-策划] 2025年各机组检修计划

```sql
SELECT ab.boiler_name AS 机组名称, ob.overhaul_name AS 检修名称, ob.overhaul_level AS 检修等级, ob.begin_date AS 开始日期, ob.end_date AS 结束日期, ob.status AS 检修状态, ob.defect_num AS 缺陷数, ob.tubchage_num AS 换管数 FROM overhaul_boiler ob INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id WHERE ob.overhaul_year = '2025' OR YEAR(ob.begin_date) = 2025 ORDER BY ab.boiler_name, ob.begin_date
```

### 37. [检修-缺陷] 查询1号锅炉水冷壁历史缺陷记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称, r.defect_type AS 缺陷类型, r.mark_type AS 检测类型, r.row_num AS 管屏号, r.mark_area AS 面积, r.mark_time AS 测量时间, r.hole_code AS 定位孔编码 FROM overhaul_record r INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND asd.device_name LIKE '%水冷壁%' AND r.mark_type = '2' AND IFNULL(r.del_flag, '0') = '0' ORDER BY r.mark_time DESC
```

### 38. [检修-缺陷] 过热器磨损类缺陷有哪些

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称, r.defect_type AS 缺陷类型, r.row_num AS 管屏号, r.mark_area AS 面积, r.mark_time AS 测量时间 FROM overhaul_record r INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE asd.device_name LIKE '%过热器%' AND r.mark_type = '2' AND r.defect_type = '2' AND IFNULL(r.del_flag, '0') = '0' ORDER BY r.mark_time DESC
```

### 39. [检修-缺陷] 再热器高温腐蚀缺陷记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称, r.defect_type AS 缺陷类型, r.row_num AS 管屏号, r.mark_time AS 测量时间, r.mark_area AS 面积 FROM overhaul_record r INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE asd.device_name LIKE '%再热器%' AND r.mark_type = '2' AND r.defect_type = '1' AND IFNULL(r.del_flag, '0') = '0' ORDER BY r.mark_time DESC
```

### 40. [检修-测厚] 查询2号锅炉省煤器测厚记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称, r.row_num AS 管屏号, r.mark_time AS 测量时间, rt.tube_code AS 管位置编号, rt.tube_position AS 管位置描述, rt.thickness AS 管子壁厚, rt.is_change AS 是否换管 FROM overhaul_record r INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id LEFT JOIN overhaul_record_tubes rt ON rt.overhaul_record_id = r.id WHERE ab.boiler_name LIKE '%2号锅炉%' AND asd.device_name LIKE '%省煤器%' AND r.mark_type = '1' AND IFNULL(r.del_flag, '0') = '0' ORDER BY r.mark_time DESC, rt.tube_code
```

### 41. [检修-换管] 1号锅炉最近一次检修换管明细

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称, ob.begin_date AS 检修开始日期, rt.tube_code AS 管位置编号, rt.tube_position AS 管位置描述, rt.thickness AS 管子壁厚, rt.length AS 长度, rt.is_change AS 是否换管 FROM overhaul_record_tubes rt INNER JOIN overhaul_record r ON rt.overhaul_record_id = r.id INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND rt.is_change = 1 AND IFNULL(r.del_flag, '0') = '0' AND ob.begin_date = ( SELECT MAX(ob2.begin_date) FROM overhaul_boiler ob2 INNER JOIN account_boiler ab2 ON ob2.boiler_id = ab2.boiler_id WHERE ab2.boiler_name LIKE '%1号锅炉%' ) ORDER BY asd.device_name, rt.tube_code
```

### 42. [检修-换管] 统计各机组历次检修换管数量

```sql
SELECT ab.boiler_name AS 机组名称, ob.overhaul_name AS 检修名称, ob.overhaul_year AS 检修年份, ob.tubchage_num AS 换管数, ob.defect_num AS 缺陷数, ob.legacy_defect_num AS 遗留缺陷数 FROM overhaul_boiler ob INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id ORDER BY ab.boiler_name, ob.overhaul_year DESC
```

### 43. [检修-减薄] 查询水冷壁壁厚减薄速率偏高的管子

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, otr.row_num AS 第几排, otr.pipe_num AS 第几根, otr.wall_thickness AS 原始壁厚, otr.wall_thickness_measure AS 测量壁厚, otr.wall_thickness_limit AS 壁厚限值, otr.wall_thickness_rate AS 减薄速率, otr.residual_life AS 剩余寿命, otr.last_measure_date AS 最后测量时间 FROM overhaul_thickness_rate otr INNER JOIN account_boiler ab ON otr.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON otr.device_id = asd.device_id WHERE asd.device_name LIKE '%水冷壁%' AND otr.wall_thickness_rate IS NOT NULL ORDER BY otr.wall_thickness_rate DESC LIMIT 50
```

### 44. [检修-减薄] 1号锅炉过热器剩余寿命较短的管段

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, otr.row_num AS 第几排, otr.pipe_num AS 第几根, otr.wall_thickness_measure AS 测量壁厚, otr.wall_thickness_rate AS 减薄速率, otr.residual_life AS 剩余寿命, otr.last_measure_date AS 最后测量时间 FROM overhaul_thickness_rate otr INNER JOIN account_boiler ab ON otr.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON otr.device_id = asd.device_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND asd.device_name LIKE '%过热器%' AND otr.residual_life IS NOT NULL ORDER BY otr.residual_life ASC LIMIT 50
```

### 45. [检修-位置] 查询1号锅炉水冷壁检测位置及原始壁厚

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, loc.name AS 位置名称, loc.code AS 编码, loc.row_count AS 总排数, loc.wall_thickness AS 原始壁厚, loc.wall_thickness_limit AS 壁厚限值, loc.wall_thickness_rate AS 减薄速率 FROM overhaul_new_checklocation loc INNER JOIN account_boiler ab ON loc.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON loc.device_id = asd.device_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND asd.device_name LIKE '%水冷壁%' AND IFNULL(loc.del_flag, 0) = 0 ORDER BY loc.name
```

### 46. [检修-蠕胀] 再热器蠕胀速率较大的测点位置

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, otr.row_num AS 第几排, otr.pipe_num AS 第几根, otr.out_thickness AS 原始外径, otr.out_thickness_measure AS 测量外径, otr.out_thickness_rate AS 蠕胀速率, otr.last_measure_date AS 最后测量时间 FROM overhaul_thickness_rate otr INNER JOIN account_boiler ab ON otr.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON otr.device_id = asd.device_id WHERE asd.device_name LIKE '%再热器%' AND otr.out_thickness_rate IS NOT NULL ORDER BY otr.out_thickness_rate DESC LIMIT 50
```

### 47. [启停] 查询近一年1号锅炉启停机记录

```sql
SELECT ab.boiler_name AS 机组名称, ss.start_date AS 启机时间, ss.stop_date AS 停机时间, ss.status AS 状态, ss.stop_reason AS 停机原因 FROM monitor_boiler_start_stop ss INNER JOIN account_boiler ab ON ss.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND (ss.start_date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY) OR ss.stop_date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)) ORDER BY IFNULL(ss.start_date, ss.stop_date) DESC
```

### 48. [启停] 本月各机组停机原因

```sql
SELECT ab.boiler_name AS 机组名称, ss.stop_date AS 停机时间, ss.stop_reason AS 停机原因, ss.status AS 状态 FROM monitor_boiler_start_stop ss INNER JOIN account_boiler ab ON ss.boiler_id = ab.boiler_id WHERE ss.status = '0' AND ss.stop_date >= DATE_FORMAT(CURDATE(), '%Y-%m-01') AND ss.stop_date < CURDATE() + INTERVAL 1 DAY ORDER BY ss.stop_date DESC
```

### 49. [吹灰] 查询1号锅炉吹灰器台账

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, sb.blower_name AS 吹灰器名称, sb.blower_code AS 吹灰器编号, sb.blower_type AS 吹灰器类型, sb.location AS 所在位置, sb.model AS 型号 FROM base_soot_blower sb INNER JOIN account_boiler ab ON sb.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON sb.device_id = asd.device_id WHERE ab.boiler_name LIKE '%1号锅炉%' ORDER BY asd.device_name, sb.blower_code
```

### 50. [吹灰] 昨天1号锅炉吹灰运行记录

```sql
SELECT ab.boiler_name AS 机组名称, sb.blower_name AS 吹灰器名称, sb.blower_code AS 吹灰器编号, rr.start_time AS 吹灰开始时间, rr.end_time AS 吹灰结束时间, rr.blowing_duration AS 吹灰时长_秒, rr.current_a AS 工作电流_A FROM monitor_soot_blower_run_record rr INNER JOIN base_soot_blower sb ON rr.blower_id = sb.blower_id INNER JOIN account_boiler ab ON sb.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND rr.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND rr.start_time < CURDATE() ORDER BY rr.start_time DESC
```

### 51. [吹灰] 近7天水冷壁区域吹灰次数统计

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, sb.blower_name AS 吹灰器名称, COUNT(*) AS 吹灰次数, ROUND(AVG(rr.blowing_duration) / 60, 1) AS 平均吹灰时长_分钟 FROM monitor_soot_blower_run_record rr INNER JOIN base_soot_blower sb ON rr.blower_id = sb.blower_id INNER JOIN account_boiler ab ON sb.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON sb.device_id = asd.device_id WHERE asd.device_name LIKE '%水冷壁%' AND rr.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND rr.start_time < CURDATE() GROUP BY ab.boiler_name, asd.device_name, sb.blower_name ORDER BY 吹灰次数 DESC
```

### 52. [磨煤机] 查询2号锅炉磨煤机台账

```sql
SELECT ab.boiler_name AS 机组名称, cm.mill_name AS 磨煤机名称, cm.mill_code AS 磨煤机编码, cm.mill_type AS 磨煤机类型, cm.location AS 所在位置, cm.putinto_date AS 投产日期 FROM base_coal_mill cm INNER JOIN account_boiler ab ON cm.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%2号锅炉%' ORDER BY cm.mill_code
```

### 53. [磨煤机] 昨天1号锅炉磨煤机平均电流和给煤量

```sql
SELECT ab.boiler_name AS 机组名称, cm.mill_name AS 磨煤机名称, ROUND(AVG(rr.current_a), 2) AS 平均电流_A, ROUND(AVG(rr.coal_flow_tonh), 2) AS 平均给煤量_tph, ROUND(AVG(rr.boiler_mw), 2) AS 平均负荷_MW, COUNT(*) AS 记录条数 FROM monitor_coal_mill_run_record rr INNER JOIN base_coal_mill cm ON rr.mill_id = cm.mill_id INNER JOIN account_boiler ab ON cm.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND rr.record_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND rr.record_time < CURDATE() GROUP BY ab.boiler_name, cm.mill_name ORDER BY cm.mill_name
```

### 54. [泄爆] 查询1号锅炉历史泄爆泄漏记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ol.leakage_date AS 泄漏时间, ol.leakage_descrip AS 位置描述, ol.row_num AS 第几排, ol.pipe_num AS 第几根, ol.leakage_reason AS 泄漏原因, ol.reason_type AS 原因分类, ol.handling_method AS 处理方法, ol.is_abnormal_stop AS 是否非停 FROM overhual_leakage ol INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id WHERE ab.boiler_name LIKE '%1号锅炉%' ORDER BY ol.leakage_date DESC
```

### 55. [泄爆] 近三年水冷壁泄漏泄爆记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ol.leakage_date AS 泄漏时间, ol.leakage_descrip AS 位置描述, ol.row_num AS 第几排, ol.pipe_num AS 第几根, ol.leakage_reason AS 泄漏原因, ol.reason_type AS 原因分类, ol.is_abnormal_stop AS 是否非停 FROM overhual_leakage ol INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id WHERE asd.device_name LIKE '%水冷壁%' AND ol.leakage_date >= DATE_SUB(CURDATE(), INTERVAL 3 YEAR) ORDER BY ol.leakage_date DESC
```

### 56. [泄爆] 过热器爆管泄漏原因分类统计

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ol.reason_type AS 原因分类, COUNT(*) AS 记录数 FROM overhual_leakage ol INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id WHERE asd.device_name LIKE '%过热器%' GROUP BY ab.boiler_name, asd.device_name, ol.reason_type ORDER BY 记录数 DESC
```

### 57. [超温-口语] 查一下昨天1号炉超温情况

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃, t.limit_duration AS 超温时长_秒, t.mw_value AS 负荷_MW FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE ab.boiler_name LIKE '%1号锅炉%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY 超温差值_℃ DESC
```

### 58. [超温-口语] 一号机组前天水冷壁有没有超温

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, t.limit_duration AS 超温时长_秒 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE ab.boiler_name LIKE '%1号锅炉%' AND asd.device_name LIKE '%水冷壁%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 2 DAY) AND t.start_time < DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.highest_temp > t.limit_temp ORDER BY t.start_time
```

### 59. [超温-口语] 上周全厂超温最多的受热面是哪个

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id WHERE t.start_time >= DATE_SUB(DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY), INTERVAL 7 DAY) AND t.start_time < DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY) AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name, asd.device_name ORDER BY 超温次数 DESC LIMIT 10
```

### 60. [超温-口语] 今天到目前为止2号锅炉超温了几次

```sql
SELECT ab.boiler_name AS 机组名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp) AS 最高壁温_℃, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%2号锅炉%' AND t.start_time >= CURDATE() AND t.start_time < CURDATE() + INTERVAL 1 DAY AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name
```

### 61. [超温-口语] 近三天高温过热器超温差值超过10度的记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE (asd.device_name LIKE '%高温过热器%' OR asd.device_name LIKE '%高过%') AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 3 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp AND (t.highest_temp - t.limit_temp) >= 10 ORDER BY 超温差值_℃ DESC
```

### 62. [台账-口语] 全厂锅炉清单

```sql
SELECT ab.boiler_name AS 机组名称, ab.boiler_code AS 锅炉编号, ab.boiler_model AS 型号, ab.edfh AS 额定负荷_MW, ab.run_date AS 投产日期 FROM account_boiler ab ORDER BY ab.sort_by, ab.boiler_name
```

### 63. [台账-口语] 1号炉四管受热面有哪些

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, asd.device_code AS 设备编码, asd.device_type AS 设备类型 FROM account_static_device asd INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND (asd.device_name LIKE '%水冷壁%' OR asd.device_name LIKE '%过热器%' OR asd.device_name LIKE '%再热器%' OR asd.device_name LIKE '%省煤器%') ORDER BY asd.sort_by, asd.device_name
```

### 64. [检修-口语] 最近一次检修发现了多少缺陷

```sql
SELECT ab.boiler_name AS 机组名称, ob.overhaul_name AS 检修名称, ob.begin_date AS 开始日期, ob.end_date AS 结束日期, ob.defect_num AS 缺陷数, ob.tubchage_num AS 换管数, ob.legacy_defect_num AS 遗留缺陷数 FROM overhaul_boiler ob INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id INNER JOIN ( SELECT boiler_id, MAX(begin_date) AS max_begin FROM overhaul_boiler GROUP BY boiler_id ) latest ON latest.boiler_id = ob.boiler_id AND ob.begin_date = latest.max_begin ORDER BY ab.boiler_name
```

### 65. [检修-口语] 水冷壁有没有需要换管的管子

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称, rt.tube_code AS 管位置编号, rt.tube_position AS 管位置描述, rt.thickness AS 管子壁厚, rt.is_change AS 是否换管 FROM overhaul_record_tubes rt INNER JOIN overhaul_record r ON rt.overhaul_record_id = r.id INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE asd.device_name LIKE '%水冷壁%' AND rt.is_change = 1 AND IFNULL(r.del_flag, '0') = '0' ORDER BY ob.begin_date DESC, rt.tube_code LIMIT 100
```

### 66. [泄爆-口语] 有没有因为四管泄漏导致非停的记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ol.leakage_date AS 泄漏时间, ol.leakage_descrip AS 位置描述, ol.leakage_reason AS 泄漏原因, ol.reason_type AS 原因分类, ol.is_abnormal_stop AS 是否非停, ol.handling_method AS 处理方法 FROM overhual_leakage ol INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id WHERE ol.is_abnormal_stop = '1' AND (asd.device_name LIKE '%水冷壁%' OR asd.device_name LIKE '%过热器%' OR asd.device_name LIKE '%再热器%' OR asd.device_name LIKE '%省煤器%' OR asd.device_name IS NULL) ORDER BY ol.leakage_date DESC
```

### 67. [超温-等级] 近7天按异常等级统计超温测点数量

```sql
SELECT ab.boiler_name AS 机组名称, SUM(CASE WHEN pt.max_delta >= 5 AND pt.max_delta < 10 THEN 1 ELSE 0 END) AS Ⅰ级数量, SUM(CASE WHEN pt.max_delta >= 10 AND pt.max_delta < 20 THEN 1 ELSE 0 END) AS Ⅱ级数量, SUM(CASE WHEN pt.max_delta >= 20 AND pt.max_delta < 40 THEN 1 ELSE 0 END) AS Ⅲ级数量, SUM(CASE WHEN pt.max_delta >= 40 THEN 1 ELSE 0 END) AS Ⅳ级数量 FROM ( SELECT t.boiler_id, t.pi_code, MAX(t.highest_temp - t.limit_temp) AS max_delta FROM monitor_hotarea_temp t WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY t.boiler_id, t.pi_code ) pt INNER JOIN account_boiler ab ON pt.boiler_id = ab.boiler_id GROUP BY ab.boiler_name ORDER BY ab.boiler_name
```

### 68. [超温-区域] 近7天1号锅炉各区域超温点数和最大时长

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, COUNT(DISTINCT t.pi_code) AS 超温点数, MAX(t.highest_temp) AS 最大超温值_℃, ROUND(MAX(t.limit_duration) / 60, 0) AS 最大连续超温时长_分钟, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name, asd.device_name ORDER BY 最大连续超温时长_分钟 DESC
```

### 69. [台账-图档] 查询1号锅炉检修相关图档资料

```sql
SELECT ab.boiler_name AS 机组名称, ba.file_name AS 资料名称, ba.file_catalogue AS 资料目录, ba.file_type AS 资料类型, ba.article_year AS 检修年份, ba.create_time AS 创建时间 FROM base_archives ba INNER JOIN account_boiler ab ON ba.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND (ba.file_catalogue LIKE '%检修%' OR ba.file_catalogue LIKE '%图档%') ORDER BY ba.article_year DESC, ba.create_time DESC
```

### 70. [防磨防爆] 查询防磨防爆小组成员

```sql
SELECT g.group_name AS 小组名称, g.group_type AS 小组类型, m.member_name AS 成员姓名, m.member_role AS 组内角色, m.member_station AS 所属岗位, m.telephone AS 手机号 FROM account_group_member m INNER JOIN account_group g ON m.group_id = g.id ORDER BY g.sort_by, m.sort_by, m.member_name
```

### 71. [超温-负荷] 近7天超温发生时负荷大于400MW的记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, t.mw_value AS 负荷_MW, t.steam_pressure_value AS 主汽压力_MPa FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp AND t.mw_value >= 400 ORDER BY t.mw_value DESC, t.start_time DESC
```

### 72. [超温-差值40] 近30天是否出现超温差值大于等于40度的临界超温

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃, t.mw_value AS 负荷_MW FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp AND (t.highest_temp - t.limit_temp) >= 40 ORDER BY 超温差值_℃ DESC
```

### 73. [超温-前墙] 昨天1号锅炉前墙出口壁温超温明细

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, t.limit_duration AS 超温时长_秒 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE ab.boiler_name LIKE '%1号锅炉%' AND (IFNULL(btp.point_name, '') LIKE '%前墙%' OR IFNULL(btp.point_name, '') LIKE '%出口%') AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY t.highest_temp - t.limit_temp DESC
```

### 74. [超温-后墙] 近7天水冷壁后墙超温统计

```sql
SELECT ab.boiler_name AS 机组名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE asd.device_name LIKE '%水冷壁%' AND (IFNULL(btp.point_name, '') LIKE '%后墙%' OR asd.device_name LIKE '%后墙%') AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name ORDER BY 超温次数 DESC
```

### 75. [超温-侧墙] 本月水冷壁侧墙超温测点列表

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码, COUNT(*) AS 超温次数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE asd.device_name LIKE '%水冷壁%' AND (IFNULL(btp.point_name, '') LIKE '%侧墙%' OR asd.device_name LIKE '%侧墙%') AND t.start_time >= DATE_FORMAT(CURDATE(), '%Y-%m-01') AND t.start_time < CURDATE() + INTERVAL 1 DAY AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name, asd.device_name, btp.point_name, t.pi_code ORDER BY 最大超温差值_℃ DESC
```

### 76. [超温-低过] 近7天低温过热器超温明细

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, (t.highest_temp - t.limit_temp) AS 超温差值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE (asd.device_name LIKE '%低温过热器%' OR asd.device_name LIKE '%低过%') AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY t.start_time DESC
```

### 77. [超温-包墙] 昨天包墙过热器有没有超温

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE asd.device_name LIKE '%包墙%' AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp ORDER BY t.highest_temp - t.limit_temp DESC
```

### 78. [超温-对比] 昨天1号和2号锅炉超温次数对比

```sql
SELECT ab.boiler_name AS 机组名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃, ROUND(AVG(t.mw_value), 2) AS 平均负荷_MW FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id WHERE (ab.boiler_name LIKE '%1号锅炉%' OR ab.boiler_name LIKE '%2号锅炉%') AND t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name ORDER BY ab.boiler_name
```

### 79. [超温-夜间] 昨天夜间20点到今天6点的超温记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, t.mw_value AS 负荷_MW FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) + INTERVAL 20 HOUR AND t.start_time < CURDATE() + INTERVAL 6 HOUR AND t.highest_temp > t.limit_temp ORDER BY t.start_time
```

### 80. [台账-二级受热面] 查询标记为二级受热面的设备

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, asd.device_code AS 设备编码, asd.device_type AS 设备类型, asd.device_level AS 设备级别 FROM account_static_device asd INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE asd.device_type LIKE '%二级%' OR asd.device_level LIKE '%二级%' ORDER BY ab.boiler_name, asd.device_name
```

### 81. [台账-管径] 水冷壁管排管直径和壁厚

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, adp.piperow_name AS 管排名称, adp.piperow_diameter AS 管直径, adp.piperow_thickness AS 管厚度, adp.model AS 规格材质 FROM account_device_piperow adp INNER JOIN account_static_device asd ON adp.device_id = asd.device_id INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE asd.device_name LIKE '%水冷壁%' ORDER BY ab.boiler_name, adp.piperow_name
```

### 82. [台账-焊口材质] 过热器焊口前后端材质不一致的清单

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, adw.weld_name AS 焊口名称, adw.weld_model_front AS 前端材质, adw.weld_model_end AS 后端材质, adw.weld_location AS 焊口位置 FROM account_device_weld adw INNER JOIN account_static_device asd ON adw.device_id = asd.device_id INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE asd.device_name LIKE '%过热器%' AND IFNULL(adw.weld_model_front, '') <> IFNULL(adw.weld_model_end, '') ORDER BY ab.boiler_name, adw.weld_name
```

### 83. [检修-结渣] 查询结渣类缺陷记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称, r.row_num AS 管屏号, r.mark_area AS 面积, r.mark_time AS 测量时间 FROM overhaul_record r INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE r.mark_type = '2' AND r.defect_type = '3' AND IFNULL(r.del_flag, '0') = '0' ORDER BY r.mark_time DESC
```

### 84. [检修-蠕变缺陷] 查询蠕变类缺陷分布在哪些受热面

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, COUNT(*) AS 蠕变缺陷数 FROM overhaul_record r INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE r.mark_type = '2' AND r.defect_type = '4' AND IFNULL(r.del_flag, '0') = '0' GROUP BY ab.boiler_name, asd.device_name ORDER BY 蠕变缺陷数 DESC
```

### 85. [检修-变形] 管道变形缺陷明细

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ob.overhaul_name AS 检修名称, r.row_num AS 管屏号, r.mark_area AS 面积, r.mark_time AS 测量时间 FROM overhaul_record r INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE r.mark_type = '2' AND r.defect_type = '5' AND IFNULL(r.del_flag, '0') = '0' ORDER BY r.mark_time DESC
```

### 86. [检修-壁厚低于限值] 测量壁厚低于限值的管子

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, otr.row_num AS 第几排, otr.pipe_num AS 第几根, otr.wall_thickness_measure AS 测量壁厚, otr.wall_thickness_limit AS 壁厚限值, otr.wall_thickness_rate AS 减薄速率, otr.residual_life AS 剩余寿命 FROM overhaul_thickness_rate otr INNER JOIN account_boiler ab ON otr.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON otr.device_id = asd.device_id WHERE otr.wall_thickness_measure IS NOT NULL AND otr.wall_thickness_limit IS NOT NULL AND otr.wall_thickness_measure < otr.wall_thickness_limit ORDER BY (otr.wall_thickness_limit - otr.wall_thickness_measure) DESC LIMIT 100
```

### 87. [检修-A级] 查询A级检修策划及缺陷换管数

```sql
SELECT ab.boiler_name AS 机组名称, ob.overhaul_name AS 检修名称, ob.overhaul_level AS 检修等级, ob.overhaul_year AS 检修年份, ob.begin_date AS 开始日期, ob.end_date AS 结束日期, ob.defect_num AS 缺陷数, ob.tubchage_num AS 换管数 FROM overhaul_boiler ob INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id WHERE ob.overhaul_level LIKE '%A%' ORDER BY ob.overhaul_year DESC, ab.boiler_name
```

### 88. [吹灰-电流] 近7天吹灰工作电流异常偏高的记录

```sql
SELECT ab.boiler_name AS 机组名称, sb.blower_name AS 吹灰器名称, rr.start_time AS 吹灰开始时间, rr.current_a AS 工作电流_A, rr.blowing_duration AS 吹灰时长_秒 FROM monitor_soot_blower_run_record rr INNER JOIN base_soot_blower sb ON rr.blower_id = sb.blower_id INNER JOIN account_boiler ab ON sb.boiler_id = ab.boiler_id WHERE rr.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND rr.start_time < CURDATE() AND rr.current_a IS NOT NULL ORDER BY rr.current_a DESC LIMIT 50
```

### 89. [磨煤机-给煤] 昨天各磨煤机最大给煤量

```sql
SELECT ab.boiler_name AS 机组名称, cm.mill_name AS 磨煤机名称, MAX(rr.coal_flow_tonh) AS 最大给煤量_tph, ROUND(AVG(rr.coal_flow_tonh), 2) AS 平均给煤量_tph, ROUND(AVG(rr.current_a), 2) AS 平均电流_A FROM monitor_coal_mill_run_record rr INNER JOIN base_coal_mill cm ON rr.mill_id = cm.mill_id INNER JOIN account_boiler ab ON cm.boiler_id = ab.boiler_id WHERE rr.record_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND rr.record_time < CURDATE() GROUP BY ab.boiler_name, cm.mill_name ORDER BY ab.boiler_name, 最大给煤量_tph DESC
```

### 90. [泄爆-排管] 查询泄漏位置在第几排第几根的记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ol.leakage_date AS 泄漏时间, ol.row_num AS 第几排, ol.pipe_num AS 第几根, ol.relative_pipe_num AS 相对吹灰器根数, ol.leakage_descrip AS 位置描述, ol.leakage_reason AS 泄漏原因 FROM overhual_leakage ol INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id WHERE ol.row_num IS NOT NULL OR ol.pipe_num IS NOT NULL ORDER BY ol.leakage_date DESC
```

### 91. [泄爆-处理方法] 泄爆泄漏后采用换管处理的记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, ol.leakage_date AS 泄漏时间, ol.handling_method AS 处理方法, ol.leakage_reason AS 泄漏原因, ol.reason_type AS 原因分类 FROM overhual_leakage ol INNER JOIN account_boiler ab ON ol.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON ol.device_id = asd.device_id WHERE ol.handling_method LIKE '%换管%' ORDER BY ol.leakage_date DESC
```

### 92. [超温-主汽压力] 近7天超温时主汽压力高于24MPa的记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.steam_pressure_value AS 主汽压力_MPa, t.mw_value AS 负荷_MW, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp AND t.steam_pressure_value >= 24 ORDER BY t.steam_pressure_value DESC
```

### 93. [超温-周期计数] 昨天周期内超限计数大于1的测点

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.start_time AS 超温开始时间, t.number AS 周期内超限计数, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 1 DAY) AND t.start_time < CURDATE() AND t.highest_temp > t.limit_temp AND t.number > 1 ORDER BY t.number DESC, t.start_time
```

### 94. [台账-额定蒸发量] 各锅炉额定蒸发量和额定负荷

```sql
SELECT ab.boiler_name AS 机组名称, ab.edfh AS 额定负荷_MW, ab.edzfl AS 额定蒸发量, ab.capacity AS 容量, ab.boiler_model AS 型号 FROM account_boiler ab ORDER BY ab.sort_by, ab.boiler_name
```

### 95. [检修-遗留缺陷] 历次检修遗留缺陷数不为零的记录

```sql
SELECT ab.boiler_name AS 机组名称, ob.overhaul_name AS 检修名称, ob.overhaul_year AS 检修年份, ob.legacy_defect_num AS 遗留缺陷数, ob.defect_num AS 缺陷数, ob.tubchage_num AS 换管数 FROM overhaul_boiler ob INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id WHERE IFNULL(ob.legacy_defect_num, 0) > 0 ORDER BY ob.legacy_defect_num DESC, ob.overhaul_year DESC
```

### 96. [超温-全厂今日] 今天全厂到目前为止的超温概况

```sql
SELECT ab.boiler_name AS 机组名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp) AS 最高壁温_℃, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃, ROUND(MAX(t.limit_duration) / 60, 0) AS 最大连续超温时长_分钟 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id WHERE t.start_time >= CURDATE() AND t.start_time < CURDATE() + INTERVAL 1 DAY AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name ORDER BY 超温次数 DESC
```

### 97. [检修-缺陷类型] 统计各受热面缺陷类型数量

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, CASE r.defect_type WHEN '1' THEN '高温腐蚀' WHEN '2' THEN '磨损' WHEN '3' THEN '结渣' WHEN '4' THEN '蠕变' WHEN '5' THEN '管道变形' ELSE CONCAT('其他:', IFNULL(r.defect_type, '')) END AS 缺陷类型名称, COUNT(*) AS 数量 FROM overhaul_record r INNER JOIN overhaul_boiler ob ON r.overhaul_id = ob.overhaul_id INNER JOIN account_boiler ab ON ob.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON r.device_id = asd.device_id WHERE r.mark_type = '2' AND IFNULL(r.del_flag, '0') = '0' GROUP BY ab.boiler_name, asd.device_name, r.defect_type ORDER BY 数量 DESC
```

### 98. [超温-测点编码] 查询测点编码10HAD11CT109最近的超温记录

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, IFNULL(btp.point_name, t.pi_code) AS 测点名称, t.pi_code AS 测点编码, t.start_time AS 超温开始时间, t.end_time AS 超温结束时间, t.highest_temp AS 最高壁温_℃, t.limit_temp AS 当前限值_℃, t.limit_duration AS 超温时长_秒 FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id LEFT JOIN account_static_device asd ON t.device_id = asd.device_id LEFT JOIN base_temp_point btp ON t.pi_code = btp.point_code AND (t.device_id IS NULL OR btp.device_id = t.device_id) WHERE t.pi_code = '10HAD11CT109' AND t.highest_temp > t.limit_temp ORDER BY t.start_time DESC LIMIT 50
```

### 99. [台账-集箱全厂] 全厂省煤器集箱工作压力

```sql
SELECT ab.boiler_name AS 机组名称, asd.device_name AS 受热面名称, adb.pipebox_name AS 集箱名称, adb.work_pressure AS 工作压力, adb.design_pressure AS 设计压力, adb.design_temp AS 设计温度 FROM account_device_pipebox adb INNER JOIN account_static_device asd ON adb.device_id = asd.device_id INNER JOIN account_boiler ab ON asd.boiler_id = ab.boiler_id WHERE asd.device_name LIKE '%省煤器%' ORDER BY ab.boiler_name, adb.pipebox_name
```

### 100. [超温-上周] 上月1号锅炉超温次数和最大差值

```sql
SELECT ab.boiler_name AS 机组名称, COUNT(*) AS 超温次数, COUNT(DISTINCT t.pi_code) AS 超温测点数, MAX(t.highest_temp - t.limit_temp) AS 最大超温差值_℃, MAX(t.highest_temp) AS 最高壁温_℃ FROM monitor_hotarea_temp t INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id WHERE ab.boiler_name LIKE '%1号锅炉%' AND t.start_time >= DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 1 MONTH), '%Y-%m-01') AND t.start_time < DATE_FORMAT(CURDATE(), '%Y-%m-01') AND t.highest_temp > t.limit_temp GROUP BY ab.boiler_name
```
