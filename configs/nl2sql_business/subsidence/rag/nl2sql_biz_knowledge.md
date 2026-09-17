# 地降 NL2SQL 业务知识（nl2sql_biz_knowledge）
# RAG 摄入命名空间：nl2sql_biz_knowledge；含三维口径与层位规则。
# 业务包说明见上级「配置项说明.md」。

## 监测类型与主辅关系

| 类型 | 表 | 主指标 | 说明 |
|------|-----|--------|------|
| 分层标 | t_data_wash_fcb | total_settle | **沉降主数据**，泛化问沉降默认此表 |
| 基岩标 | t_data_wash_jyb | total_settle | 对照分层标；标编号常为 `J*` |
| GNSS | t_data_wash_gnss | displacement_2d/3d | 位移专题，勿与 fcb 混用 |
| 地下水 | t_data_wash_dxswj | deep, elevation | 水位辅助 |
| 孔隙水 | t_data_wash_kxsylj | pressure | 孔压辅助 |
| 光纤 | t_data_wash_gq | total_settle | 光纤沉降 |
| 气象 | t_data_wash_qxz | temp, real_time_rain | 降水气温辅助 |

## 沉降口径（对齐季报脚本，2026-09 起）

- **周期变化量**：`Δ = total_settle(窗初) − total_settle(窗末)`（mm），即时间窗内**最早有效点减去最晚有效点**（**初 − 末**）。
- **符号**：`Δ > 0` → **下沉倾向**；`Δ < 0` → **回弹/抬升倾向**（与 `季度报告分层标处理.py` 一致）。
- **单点累计**：某时刻 `total_settle` 快照（不是周期差）。
- 问「沉降了多少」未指明监测类型时 → 分层标 `t_data_wash_fcb`。
- 问 GNSS/位移 → `t_data_wash_gnss`，禁止用 fcb.total_settle 代替。
- **口径变更说明**：旧文档曾写「终−初、负值下沉」；现网以本初−末为准。相对旧口径：`Δ_新 = −Δ_旧`。

## 公共表字段说明

- **`project_name`**：站点名称，格式如 `F27(大鲁店)`；与 `t_station.name` 等值关联。
- **`station_name`**：分层标/基岩标**标编号**，格式如 `F27-8`、`J8-1`（库注释若写「站点名称」亦按标编号理解）。
- 禁止把 `station_name` 当成行政区或站点中文名单独过滤行政区。

## 分层标三维计算汇总逻辑

### A. 分层标明细维度

- 对象：某个 `station_name`（标编号）。
- 数据：`t_data_wash_fcb`（`J*` 优先 `t_data_wash_jyb`）该标的 `total_settle` 时间序列。
- 周期沉降：对该标计算 `Δ`（初 − 末）。

### B. 监测站点维度（地面 / 站代表沉降）

- 对象：某个 `project_name`。
- **代表标**：层位字典中该站 **`monitor_layer = 0`** 的 `station_name`（≡ 该站深度最小分层标）。
- 站点周期沉降 = 该代表标上的 `Δ`。
- **禁止**：对同一 `project_name` 下所有 `station_name` 做 AVG/SUM 当作站点沉降。
- 代表标清单见同目录 `nl2sql_biz_knowledge_fcb_layer0_stations.md`；权威映射见 `semantic/dimensions/fcb_layer_map.yaml`。

### C. 压缩层 / 层间压缩维度

- 对象：某站相邻监测层位区间 `i → i+1`（如 `0-1`）。
- 边界标：字典中 `monitor_layer=i` 与 `i+1` 对应的两个 `station_name`（一侧可为基岩标 `J*`）。
- **周期层间压缩量**：`compress(i→i+1) = Δ(层位 i) − Δ(层位 i+1)`。
- **不是**：单独取「标注了监测层位=k 的那根标」的累计 `total_settle` 当作第 k 压缩层沉降。
- `monitor_layer` 为空的中间辅助标：可查该标自身沉降，**不参与**站点代表与层间差。
- 层位不连续（备注「无第四」等）：只算存在的连续对，勿臆造。

### 问句映射

| 用户说法 | 维度 |
|----------|------|
| 某分层标 / `F8-7` / 标编号 | A |
| 某站点沉降 / 地面沉降 / 各站排行 | B（层位 0） |
| 压缩层 / 层间沉降 / 第 1、2 层压缩 | C |
| 「第 N 层分层标」 | 带「压缩层/层位」→ C；形如 `F8-N` → A |

## 行政区与站点范围

- 事实表 **`project_name`** 与 **`t_station.name`** 等值关联。
- 行政区过滤：`t_station.area`（标准名如「朝阳区」「通州区」）；层位字典中的 `district_raw` 可能是简称，**不以之为 SQL 过滤真源**。
- **监测方式官方站点覆盖**：`semantic/dimensions/device_station_map.yaml`（`device_type → project_name[]`）。查询某监测方式站点时，先取覆盖名单再与 `t_station` 匹配；**不以**事实表「当前有数据」代替官方覆盖。GNSS 一期名单为空占位。
- 北京市 16 区：东城、西城、朝阳、丰台、石景山、海淀、门头沟、房山、通州、顺义、昌平、大兴、怀柔、平谷、密云、延庆。

## 层位字典（配置文件）

- 权威文件：`configs/nl2sql_business/subsidence/semantic/dimensions/fcb_layer_map.yaml`（由 `分层标层位分析.xls` 转换入库；层位变更时更新该 YAML）。
- 字段：`project_name`、`station_name`、`borehole_type`、`depth_m`、`monitor_layer`、`note`、`district_raw`。
- **一期不 JOIN 该文件为库表**；生成 SQL 时按本知识与层位 0 清单限定 `station_name`（或写死代表标）。
- 与 `device_station_map` 分工：后者管「哪些场地具备该监测方式」；本文件管分层标「标编号 / 层位」。

## 报告分析类型（综合分析）

- `subsidence_daily` / `weekly` / `monthly` / `quarterly` / `yearly`
- 取数计划见 `analysis_plan_subsidence_*`（prompts.yaml）
- QA 槽位五元组：`analysis_type` + `plan_item_id` + `plan_template_version`

## 时间窗

时间列统一用各事实表的 `data_time`。过滤推荐半开区间：`data_time >= @t_start AND data_time < @t_end`。

### 时间词消歧（必遵）

| 用户说法 | 含义 | PostgreSQL 推荐写法（示意） |
|----------|------|------------------------------|
| **本周 / 这周** | **自然周**（周一 00:00 ～ 下周一 00:00） | `@t_start = date_trunc('week', CURRENT_DATE)::date`<br>`@t_end = @t_start + INTERVAL '7 days'` |
| **上周** | 上一自然周 | `@t_start = date_trunc('week', CURRENT_DATE)::date - INTERVAL '7 days'`<br>`@t_end = date_trunc('week', CURRENT_DATE)::date` |
| **本月 / 这个月** | 自然月 | `date_trunc('month', CURRENT_DATE)::date` ～ `+ INTERVAL '1 month'` |
| **上月 / 上个月** | 上一自然月 | 上月月初 ～ 本月月初 |
| **本季度 / 这个季度** | 自然季度 | 季初 ～ 季初 + 3 months |
| **近 N 天 / 最近 N 天** | **滚动**近 N 天 | `NOW() - INTERVAL 'N days'` ～ `NOW()` |
| **近一周 / 最近一周 / 近7天** | **滚动**近 7 天（≠本周） | `NOW() - INTERVAL '7 days'` ～ `NOW()` |
| **近3天** | 滚动近 3 天 | `NOW() - INTERVAL '3 days'` ～ `NOW()` |

### 硬性区分

- **「本周」≠「近一周」**：本周必须用自然周起止；**禁止**用 `CURRENT_DATE - INTERVAL '7 days'`（或等价滚动 7 天）代替「本周」。
- `INTERVAL '7 days'` 仅适用于「近一周 / 最近一周 / 近7天」等**滚动窗**说法，或作为方言语法示例，**不得**默认映射到「本周」。
- 程序侧若已解析出 `this_week` / `last_week` / `this_month` 等 tag，生成 SQL 须与 tag 一致，勿自行改写成滚动窗。

## 周期沉降 SQL 禁令

适用于问「某区/全市 + 本周/本月… + 沉降最大/最小/排行」等**站点周期沉降**类问题（维度 B）。

### 必遵结构

1. 主表：`t_data_wash_fcb`；行政区经 `t_station`：`f.project_name = s.name`，`s.area = '朝阳区'`（标准全名优先，`LIKE` 仅兜底）。
2. 站点粒度：仅层位 0 代表标（`station_name IN (...)`，清单见 `nl2sql_biz_knowledge_fcb_layer0_stations.md`，按区裁剪）。
3. 同一 `project_name + station_name` 在时间窗内取**最早一条**为窗初、**最晚一条**为窗末（可用 `ROW_NUMBER` 双排序，或等价 `MIN/MAX(data_time)` 再回表）。
4. `period_settle_mm = settle_start − settle_end`（**初 − 末**）；「沉降最大」= `ORDER BY period_settle_mm DESC`（Δ 越大下沉越多）。
5. 窗内缺初或缺末观测的站点：**不参与**最大/排行（或结果中显式排除），禁止用单点 `total_settle` 冒充周期差。

### 明确禁止

| 禁止写法 | 原因 |
|----------|------|
| `settle_end − settle_start`（末−初） | 符号与现网口径相反；`DESC` 会误选回弹最大 |
| 只取 `MIN(data_time)` 作初值、**不**取窗内 `MAX(data_time)` 作末值 | 末值未钉死，结果不可复现 |
| 自连接/子查询仅按 `project_name` 对齐、**不对齐** `station_name` | 同站多标会错配初末 |
| 对同 `project_name` 下全部 `station_name` 做 `AVG/SUM` | 站点沉降必须用层位 0 代表标 |
| 用单点 `MAX(total_settle)` / 最新一条累计值当「本周沉降最大」 | 「沉降最大」指周期 `Δ`，不是累计快照 |
| 用 `CURRENT_DATE - INTERVAL '7 days'` 回答「本周」 | 本周=自然周，见上一节 |
| 把层位 0 的 `station_name`（如 `F1-7`）写进 `t_station.name` 过滤 | 标编号 ≠ 站点名；站点名是 `project_name` / `t_station.name` |

推荐完整正例见 `qa_examples_seed.md`「朝阳区本周沉降最大站点」。

## SQL 方言

- 地降库为 **PostgreSQL**：`CURRENT_DATE`、`NOW()`、`date_trunc(...)`、`INTERVAL '…'`。
- `INTERVAL '7 days'` 仅用于滚动窗（近一周等）；自然周/月/季见「时间窗」节，勿混用。
- 禁止 TiDB/MySQL 写法：`INTERVAL 7 DAY`、`DATE_SUB`、`WEEKDAY()` 等。

## 站点规模（快照）

- `t_station` 约 83 个监测站点；层位字典覆盖约 42 个分层标站点、380 条标记录。
- 词表：`scope_lexicon.json`
