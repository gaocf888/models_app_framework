# NL2SQL基座改造补充-分层标数据汇总逻辑(三维口径与层位字典)实现方案

> **版本**：2026-09-11  
> **状态**：配置已落地（2026-09-11）；**待运维**：RAG re-ingest + 抽测（见 §5.3）  
> **所属**：地降所 NL2SQL 基座 · 业务口径补强（在 P0–P5 主能力已落地之后）  
> **主方案索引**：[`NL2SQL基座改造.md`](./NL2SQL基座改造.md)（本文为其专项子方案，不替代主方案）  
> **口径真源（计算逻辑）**：`docs/地降所需求及数据相关/数据库结构及逻辑/沉降数据获取计算逻辑/季度报告分层标处理.py`  
> **层位映射真源**：同目录 `分层标层位分析.xls`（将转为项目内配置文件）
> **文档简称**：分层标三维口径与层位字典改造实现方案（文内可简称 P6 专项）

---

## 0. 背景与目标

### 0.1 问题

地降分层标业务存在 **三种 grain**（分层标明细 / 站点代表沉降 / 压缩层层间差），原先半自动季报依赖 Excel + 脚本。现网 NL2SQL 知识库与 Prompt 中：

1. **周期沉降公式写反**：写成「终值 − 初值」，与季报脚本「初值 − 终值」不一致；  
2. **正负语义写反**：写成「负值表示下沉」，与脚本符号约定不一致；  
3. **站点 / 压缩层口径缺失或易误导**：易把「全标聚合」当站点沉降，或把「单标累计」当压缩层沉降；  
4. **层位字典外挂在 xls**：库表无深度/监测层位列，仅靠 RAG 难稳定选对「层位 0」边界标。

### 0.2 目标（一期）

| # | 目标 | 策略 |
|---|------|------|
| G1 | 全链路统一周期沉降与正负口径 | 对齐 `季度报告分层标处理.py` |
| G2 | 知识库写清三维 grain + 禁则 | 更新 `nl2sql_biz_knowledge.md` 等 |
| G3 | 层位映射进配置包 | **知识库 + 配置文件**（主方案推荐短期策略①） |
| G4 | Prompt / QA / 语义指标 / 综合分析计划同步 | 避免 RAG 与 Prompt 双口径 |
| G5 | 摄入后可测 | re-ingest + 黄金问句手测 |

### 0.3 非目标（一期明确不做）

- 业务库新建维表 / 报表视图（中期策略，见 §7）  
- Chain 内强制程序改写「只能查层位 0」（二期可选）  
- 重写半自动 `季度报告分层标处理.py`（保留为口径参考）  
- 锅炉域相关文档与 Prompt  

### 0.4 已确认的统一口径（业务拍板）

对齐季报脚本：

| 项 | 约定 |
|----|------|
| 单标周期变化量 | `Δ = total_settle(窗初) − total_settle(窗末)`（**初 − 末**） |
| 下沉 / 回弹 | **Δ 为正 → 下沉倾向**；**Δ 为负 → 回弹/抬升倾向**（与脚本一致） |
| 站点沉降 | 该站 **监测层位 = 0** 的标（≡ 该站深度最小分层标）上的 `Δ`；**禁止**对同站全部 `station_name` 做 AVG/SUM |
| 压缩层 `i→i+1` | `compress = Δ(层位 i) − Δ(层位 i+1)`；仅连续层位对；**不是**单标 `total_settle` 直接当「层沉降」 |
| 字段 | `project_name` = 站点名（如 `F8(周村)`）；`station_name` = 标编号（如 `F8-10`） |

> **注意**：若历史对外材料曾写「负值=下沉」，一期以本表为准全面更正；展示层若需「下沉深度为正」的 UI，应在前端二次映射，**不要**再改回 SQL 公式。

---

## 1. 业务逻辑精炼（写入知识库的正文口径）

### 1.1 数据 grain

```text
库表行（fcb/jyb）
  = project_name（站点）× station_name（标编号）× data_time → total_settle
```

| 维度 | 对象 | 取数方式 |
|------|------|----------|
| **A. 分层标明细** | 某个 `station_name` | 直接查该标时间序列 / 周期 `Δ` |
| **B. 监测站点** | 某个 `project_name` | 经层位字典定位 **`monitor_layer=0`** 的标，再算 `Δ` |
| **C. 压缩层 / 层间** | 某站区间 `i-(i+1)` | 取层位 `i` 与 `i+1` 两边界标的 `Δ`，再相减 |

### 1.2 周期与层间公式

```text
# 单标（及站点代表标）周期变化量
Δ = total_settle_start − total_settle_end     # 窗内最早有效点 − 最晚有效点

# 相邻压缩层（监测层位连续时）
compress(i → i+1) = Δ(layer=i) − Δ(layer=i+1)
```

### 1.3 层位字典角色

来源：`分层标层位分析.xls` → 配置 `fcb_layer_map`。

| 列（配置字段） | 含义 |
|----------------|------|
| `project_name` | 站点名称，对齐库 `project_name` / `t_station.name` |
| `station_name` | 标编号，对齐库 `station_name` |
| `borehole_type` | 分层标孔 / 基岩标孔（`J*` 事实数据优先 `t_data_wash_jyb`） |
| `depth_m` | 单孔验收深度；用于理解「浅/深」，选站代表时与 layer=0 等价 |
| `monitor_layer` | `0..4` 或空；**空 = 中间辅助标，不参与站点代表与层间差** |
| `note` | 如「无第四」——缺更高层，勿臆造 |

当前快照规模（实施时以最新 xls 为准）：约 42 站、380 行标；**42/42 站「深度最小 ≡ 层位 0」**。

### 1.4 问句映射（防歧义）

| 用户说法 | 维度 |
|----------|------|
| 「F8-7 / 某号分层标」 | A |
| 「某站点沉降 / 地面沉降 / 各站排行」 | B（层位 0） |
| 「第 1、2 压缩层 / 层间沉降」 | C |
| 「第 N 层分层标」 | **歧义**：可能是标序号，也可能是 `monitor_layer=N` → Prompt/知识库约定优先规则（建议：带「压缩层/层位」走 C；带「F8-N」形态走 A） |

---

## 2. 改造范围与文件清单

### 2.1 新增（配置包）

| 路径（建议） | 说明 |
|--------------|------|
| `configs/nl2sql_business/subsidence/semantic/dimensions/fcb_layer_map.yaml` | 层位权威映射（由业务 xls 人工/一次性转换入库） |

`profile.yaml` / semantic 清单中 **登记** `fcb_layer_map` 路径（一期允许仅文档+RAG 引用，代码可不强制加载）。

### 2.2 必须修改（口径同步）

| 类别 | 路径 | 改什么 |
|------|------|--------|
| 知识库 | `configs/nl2sql_business/subsidence/rag/nl2sql_biz_knowledge.md` | 公式、正负、三维 grain、指向配置文件 |
| Schema RAG | `.../rag/nl2sql_schema.md` | 周期口径句 |
| QA 种子 | `.../rag/qa_examples_seed.md` | SQL 改为 `初−末`；站点样例约束层位 0；排序按「Δ 越大越下沉」 |
| 语义指标 | `.../semantic/metrics.yaml` | `formula_note` 与 rebound 说明 |
| NL2SQL 主 Prompt | `configs/prompts.yaml` · `nl2sql` · `v2_subsidence` | 短硬口径 3～6 行 |
| 综合分析计划 | `analysis_plan_subsidence_*` | 问句中写明初−末；「下沉最深」= `Δ` 降序 |
| 综合分析合成 | `analysis_synthesis_subsidence_*` | 去掉「终−初 / 负值下沉」 |
| 客服地降模板 | `chatbot` 相关 `subsidence_v1` | 「负值通常表示下沉」→ 新符号约定 |
| 评测 | `tests/fixtures/nl2sql_subsidence_golden_set.json` | notes / 问句覆盖站点层位 0、压缩层、符号 |

### 2.3 建议交叉修订（防文档打架）

| 文档 | 动作 |
|------|------|
| [`NL2SQL基座改造.md`](./NL2SQL基座改造.md) | 增加本期 P6、关联本文、更正验收中「周期沉降」表述 |
| [`数据查询智能体实现方案.md`](./数据查询智能体实现方案.md) | 「年沉降 = 终−初」改为引用本文口径（初−末），避免查询台与基座相反 |
| 企业级 `nl2sql_biz_knowledge` 相关说明（若有抄录旧公式） | 择机一句更正 |

### 2.4 运维动作

- 修改 RAG 源文件后：**re-ingest** `nl2sql_biz_knowledge`（建议同时核对 `nl2sql_schema`、`nl2sql_qa_examples`）  
- 部署无需改 `NL2SQL_BUSINESS_DOMAIN`；配置文件随 `configs` 挂载目录发布  

---

## 3. 分层策略实现设计（一期：知识库 + 配置文件）

### 3.1 原则

```text
计算规则与禁则  →  nl2sql_biz_knowledge.md（+ Prompt 短硬规则）
站↔标↔层位映射 →  fcb_layer_map 配置文件（权威）
xls             →  仅转换来源，不再作为运行权威
```

### 3.2 配置文件字段草案

```yaml
# fcb_layer_map.yaml
version: "2026-09-11"
source: "分层标层位分析.xls"
entries:
  - project_name: "F8(周村)"
    station_name: "F8-10"
    borehole_type: "分层标孔"
    depth_m: 2.16
    monitor_layer: 0
    note: ""
  - project_name: "F8(周村)"
    station_name: "J8-1"
    borehole_type: "基岩标孔"
    depth_m: 734.14
    monitor_layer: 4
    note: ""
  # … 其余站
```

也可使用 CSV 便于 Excel 往返；YAML 便于 semantic 目录统一。

### 3.3 一期如何被 NL2SQL「用到」

| 通道 | 做法 | 作用 |
|------|------|------|
| RAG | biz_knowledge 描述规则 + 说明映射在配置路径；可选把「每站 layer0 标编号」摘要片段摄入 | 召回口径 |
| Prompt | `v2_subsidence` / plan 写死：站点沉降必须对应层位 0；周期=初−末；正下沉 | 保底 |
| QA | 2～3 条标准 SQL/问法（站点层位 0、压缩层差） | 强示范 |
| 配置文件 | Git 可审、可再导出；**一期可不被 chain 解析** | 权威字典 |

### 3.4 二期增强（运行时注入）

1. **`semantic_layer` 加载 `fcb_layer_map`，问「站点沉降」时注入 `preferred_station_names`（层位0）；问「压缩层/层间」时注入相邻层位边界标 `compress_pairs`** — **已落地**（`align_semantics` → `schema_linker.suggested_filters` → Prompt 意图块）。  
2. 或落库 `dim_fcb_monitor_layer`，SQL 可 `JOIN`（未做）；  
3. 可选视图 `v_fcb_station_surface`（仅 layer0）（未做）。

---

## 4. Prompt / 知识库改写要点（实施时照抄结构）

### 4.1 `nl2sql_biz_knowledge.md` 应具备的章节

1. 监测类型主辅（保留）  
2. **沉降口径（更正）**：初−末；正下沉负回弹  
3. 行政区与站点（保留）+ **字段语义**（`project_name` / `station_name`）  
4. **分层标三维计算汇总逻辑**（A/B/C + 公式 + 禁则）  
5. **外挂/配置字典**：指向 `fcb_layer_map`；xls 为历史来源  
6. SQL 方言 PG（保留）

### 4.2 `v2_subsidence` 建议硬规则（示例文案）

```text
【周期沉降】Δ = 窗内 total_settle 初值 − 末值（mm）；Δ>0 下沉倾向，Δ<0 回弹倾向。
【站点沉降】仅使用该站监测层位=0（浅部代表标）对应的 station_name；禁止对同 project_name 下所有标聚合。
【压缩层】相邻监测层位 i 与 i+1 的 Δ 之差；无连续层位则不算。
【关联】行政区：JOIN t_station ON project_name=name；标编号字段为 station_name。
```

综合分析 `analysis_plan_subsidence_*` 的 `question` 字段须显式写「初值减末值」，避免模型沿用旧 QA。

---

## 5. 实施分期与任务拆解

### 5.1 阶段 P6（建议命名）

| 子阶段 | 内容 | 交付物 | 预估 |
|--------|------|--------|------|
| **P6.1** | xls → `fcb_layer_map`；profile/semantic 登记 | 配置文件 + 转换说明 | 0.5～1d |
| **P6.2** | 改 biz_knowledge / schema / metrics | 三份配置源文件 | 0.5d |
| **P6.3** | 改 qa_examples_seed + golden notes | QA/评测对齐新符号 | 0.5～1d |
| **P6.4** | 改 `v2_subsidence` + analysis_*_subsidence_* + chatbot subsidence 符号句 | prompts.yaml | 0.5～1d |
| **P6.5** | 交叉改主方案验收项 + 数据查询智能体年沉降口径引用 | 本文已列文档 | 0.5d |
| **P6.6** | re-ingest + 手测黄金问句 | 联调记录 | 0.5d |

**合计**：约 3～5 人日（不含二期代码加载层位字典）。

### 5.2 建议实施顺序

```text
1. 定稿口径（已完成，见 §0.4）
2. 生成 fcb_layer_map
3. 改 nl2sql_biz_knowledge.md
4. 同步 schema.md、metrics.yaml、qa_examples_seed.md
5. 改 prompts（v2_subsidence → plan/synthesis → chatbot）
6. 微调 golden_set；修订 NL2SQL基座改造 / 数据查询智能体交叉句
7. re-ingest nl2sql_* 
8. 手测：站点周期符号、层位0、压缩层差、报告解读不反号
```

### 5.3 验收标准（P6）

- [x] `biz_knowledge` / `schema` / `metrics` **无**「终值减初值」「负值表示下沉」旧表述  
- [x] QA 种子中周期 SQL 为 **初 − 末**；至少 1 条站点层位 0、1 条层间差说明或样例  
- [x] `fcb_layer_map` 已入库配置包（entry_count=380，layer0_station_count=42）  
- [x] `v2_subsidence` 与 `analysis_synthesis_subsidence_*` / plan 口径一致  
- [x] 客服地降模板符号句已改（chatbot + clarify_texts）  
- [ ] RAG re-ingest 完成；抽测问句生成 SQL 差值方向正确  
- [x] [`NL2SQL基座改造.md`](./NL2SQL基座改造.md) 已索引本文并将周期沉降验收改为「初−末 / 正下沉」  

---

## 6. 风险与对策

| 风险 | 对策 |
|------|------|
| 仅改知识库、未改 Prompt/QA → 模型仍按旧公式生成 | P6.3～P6.4 强制同批提交 |
| 一期不加载配置文件 → 站点层位 0 偶发漏选 | Prompt 硬规则 + QA；二期加载 map 或 DB 维表 |
| 对外历史报告习惯「负下沉」 | 在知识库「口径变更说明」写明切换日期与对照关系：`Δ_新 = −Δ_旧` |
| `J*` 落 jyb、`F*` 落 fcb 混用 | 知识库与 Prompt 写清；层间差跨表时允许同 `project_name` 分表取两边界 |
| 行政区简称（xls「平谷」）与 `t_station.area`（「平谷区」）不一致 | 配置可保留 raw；过滤仍以 `t_station.area` 为准 |
| 数据查询智能体仍写终−初 | P6.5 交叉修订，避免查询台与基座相反 |

---

## 7. 中长期演进（非本期交付）

1. **DB 维表** `dim_fcb_monitor_layer`：配置同步入库，SQL 原生 JOIN。  
2. **semantic_layer 加载 map**：站点类指标自动带 `station_name IN (layer0…)`。  
3. **报表视图**（可选）：`v_fcb_station_surface`。  
4. 压缩层专用 metric id（如 `layer_compress_mm`）写入 `metrics.yaml` 并链到双标差公式。

---

## 8. 参考材料

| 材料 | 用途 |
|------|------|
| `.../沉降数据获取计算逻辑/季度报告分层标处理.py` | 周期与层间差公式真源 |
| `.../分层标层位分析.xls` | 层位映射转换源 |
| `.../分层标456月份.xlsx` | 理解半自动输入形态（非 NL2SQL 查询对象） |
| [`NL2SQL基座改造.md`](./NL2SQL基座改造.md) | 基座总方案 |
| `configs/nl2sql_business/subsidence/rag/*` | RAG 源文件 |
| `configs/prompts.yaml` | `v2_subsidence`、`analysis_*_subsidence_*` |

---

*本文为「分层标三维口径 + 层位字典配置化」的唯一专项实现方案；与 NL2SQL 基座主方案冲突时，**周期沉降符号与三维 grain 以本文 §0.4 为准**，主方案验收项应回写对齐。*
