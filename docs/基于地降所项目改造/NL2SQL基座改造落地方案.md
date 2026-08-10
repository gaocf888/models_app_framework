# 地降所项目 — NL2SQL 基座改造落地方案  
## （聚焦：语义建模 + 显式 Schema 链接）

> **版本**：2026-08-10（修订：取消 `NL2SQL_DOMAIN_PROFILE`；锅炉/地降共用同一套「语义+链接」管线，差异仅在配置资产）  
> **范围**：仅 **NL2SQL 基座**（`app/nl2sql/*`、`NL2SQLService`、请求/响应中与本改造相关的契约扩展）。chatbot / analysis / 报告呈现等为调用方，本文只约定其如何消费新能力。  
> **决策依据**：相对现网「RAG + 反射 + 强后处理」链路，对 **NL→SQL 准确率** 真正有明显增益的是 **语义建模** 与 **显式 Schema 链接**；查询类型意图、交互澄清、结果画图等非本方案一期主体。  
> **域差异原则**：**不设** `boiler`/`subsidence` 运行时 profile 开关。改造后各项目均走同一模式；区别仅为语义字典、表白名单、范围维度字段内容不同（如机组/受热面/管排 vs 行政区/站点/GNSS）。
> **现网基线**：`enterprise-level_transformation_docs/企业级NL2SQL实现方案.md`  
> **完整五阶段讨论（背景）**：同目录 `NL2SQL基座五阶段改造方案.md`（本文是其 **精简落地版**：只落 ②③）  
> **关联资料**：  
> - `docs/地降所需求及数据相关/数据库结构及逻辑/`  
> - `docs/地降所需求及数据相关/需求梳理/智能数据查询及分析-需求剖析.md`  
> - `docs/NL2SQL自然语言时间和范围窗口解析&改写改造落地方案.md`

---

## 0. 结论与改造边界

### 0.1 一句话结论

**在现网 NL2SQL 后半段（生成 / 时间·范围改写 / 校验 / 缓存 / QA / 执行）基本冻结的前提下，向前插管两段能力：**

1. **语义建模**：把自然语言对齐到可版本化的业务语义（指标口径、同义词、单位、行政区/站点等维度码）。  
2. **显式 Schema 链接**：在进 Prompt / LLM 之前，把意图+语义钉到 **预登记白名单内的表/列/JOIN/过滤**，收窄 catalog，降低选错表、选错列、口径漂移。

### 0.2 做 / 不做

| 做（一期） | 不做（一期明确排除） |
|------------|----------------------|
| 版本化语义资产 + 运行时对齐（**各项目各配一套资产**） | 完整「查询类型」五阶段意图体系（可后续叠加） |
| `_link_schema` → `LinkedSchema` + catalog 收窄 | 基座内多轮澄清 / 等人回话 |
| 统一管线；锅炉/地降仅换资产与白名单 | **`NL2SQL_DOMAIN_PROFILE` 域运行时分流** |
| 表白名单、维度改写挂钩、trace 可审阅 | 重写 Validator / Executor / 缓存内核 |
| 总开关灰度（整链开/关语义+链接） | 基座内图表引擎 / BI 前端；一次性语义化全库 |

### 0.3 与现网主链路的挂载关系

```text
现网主链路（保留）：
  resolve_question_intent（时间规则 + 范围 rule/LLM/HITL）
    → Schema 反射
    →（可选）规划
    → RAG / QA回放 / L2·L1 缓存
    → Prompt + LLM
    → 时间·范围改写 → 校验 → 执行

本改造插管点（新增，在 RAG/Prompt 之前、意图之后）：
  resolve_question_intent
    → ★ SemanticAlign（语义建模）
    → ★ SchemaLink   （显式链接 → LinkedSchema）
    → RAG / 缓存 / Prompt（catalog 优先用 LinkedSchema 收窄结果）
    → …现网后半段不变
```

**主代码挂载点**：`NL2SQLChain.generate_sql_with_validation_context`（`app/nl2sql/chain.py`），位于 `resolve_question_intent` 成功之后、组装 `full_catalog` / 调用 LLM 之前。

---

## 1. 背景：为何只押这两项

| 能力 | 现网状态 | 对准确率 | 本期态度 |
|------|----------|----------|----------|
| 时间窗 / 锅炉式范围改写 | 已较强（规则） | 已覆盖一大类问题 | **保留**；地降扩维度槽位 |
| QA 回放 / L1·L2 缓存 / refine | 已较强 | 已知问句很稳 | **保留** |
| 隐式 Schema（反射+RAG+表白名单） | 有，但「白名单内选错表列」难挡 | 自由问数仍易错 | **升级为显式链接** |
| 业务口径（指标/单位/码表） | 散落 Prompt / biz RAG | **最大短板** | **语义层补齐** |
| 查询类型 + refuse | 基本没有 | 偏可控性，非准度主杠杆 | **延后** |

地降自由问数的典型错误形态：

- 「沉降量」与「地下水降深」混用 → **语义**  
- 问 GNSS 却扫到分层标表、或错用 `deep`/`elevation` → **链接**  
- 通州写成模糊 LIKE、站点名与 `station_id` 对不上 → **语义码表 + 链接过滤**

---

## 2. 目标与成功标准

### 2.1 目标

| 编号 | 目标 |
|------|------|
| G1 | 基座具备可版本化 **业务语义层**（指标 / 同义词 / 单位 / 维度字典），运行时写入 `parsed_intent`（含口径引用与语义版本号） |
| G2 | 基座具备显式 **`LinkedSchema`** 阶段：候选表/列/JOIN/过滤 ∩ DB 反射 ∩ 预登记白名单 |
| G3 | Prompt 注入的 schema catalog **优先且默认** 使用链接结果（可配置降级到宽 catalog） |
| G4 | **统一模式**：锅炉与地降等项目共用同一套「语义对齐 → Schema 链接 → 现网后半段」；差异只在资产路径与白名单内容 |
| G5 | 链接失败 / 语义无法对齐时返回 **机读原因**（不阻塞等待用户）；策略 `refuse` / `best_effort` 可配置 |
| G6 | 现网后半段（改写/校验/缓存/执行）保持；锅炉项目通过 **配置锅炉语义资产+白名单** 接入，而非靠 profile 退回旧路径 |

### 2.2 量化成功标准（建议黄金集验收）

在同一地降黄金集（建议 ≥30 条自由问数，含期望表列或期望结果口径）上对比 **baseline（仅现网）** vs **本改造开启**：

| 指标 | 期望 |
|------|------|
| 主表选对率 | 相对 baseline **明显提升**（目标 +15pt 起，以实测为准） |
| 关键度量列选对率 | 相对 baseline 提升 |
| 可执行率（通过校验且 SELECT 成功） | 不低于 baseline |
| 口径可追溯率 | 命中语义条目的问句 ≥ 登记核心指标的覆盖约定 |
| 锅炉黄金集（改造后配锅炉资产） | 表列/口径正确率不低于改造前基线（允许实现路径变为语义+链接） |

---

## 3. 目标架构

### 3.1 逻辑架构

```text
NL2SQLQueryRequest
  question
  (+ time_intent_text / confirmed_scope / analysis_type /
     on_link_failure? / structured_filters? …)
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│ 0. 现网意图（保留并泛化范围槽位）                              │
│    resolve_question_intent                                   │
│    · 时间：time_intent_display（规则）                       │
│    · 范围：rule / LLM / confirmed_scope（字段由语义资产定义） │
└───────────────────────────────┬─────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────┐
│ 1. ★ 语义建模 SemanticAlign（本期新增）                      │
│    输入：问句 + QuestionIntent                               │
│    资产：NL2SQL_SEMANTIC_DICT_PATH（项目部署时指向地降或锅炉）│
│    过程：同义词展开 → 指标命中 → 单位/粒度绑定 → 维度码对齐  │
│    输出：SemanticBinding（metric_id、dimension_codes、        │
│          unit、definition_ref、semantic_version、warnings）   │
└───────────────────────────────┬─────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. ★ 显式 Schema 链接 SchemaLink（本期新增）                 │
│    输入：SemanticBinding + Intent + 反射目录 + 白名单 + RAG  │
│    过程：候选表/列打分 → JOIN 路径 → 过滤列建议              │
│    输出：LinkedSchema（tables、columns、joins、filters、     │
│          confidence、fail_reason?）                          │
│    失败：on_link_failure=refuse → 短路返回；=best_effort →   │
│          降级宽 catalog 并打点告警                           │
└───────────────────────────────┬─────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. 现网后半段（冻结）                                         │
│    RAG / QA回放 / L2·L1 → Prompt（catalog←LinkedSchema）     │
│    → LLM → 时间·范围改写 → Validator → Executor              │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 项目差异如何体现（无 DOMAIN_PROFILE）

改造后 **算法管线相同**；换项目只换「配置包」：

| 配置包内容 | 地降项目示例 | 锅炉项目示例 |
|------------|--------------|--------------|
| `NL2SQL_SEMANTIC_DICT_PATH` | `configs/nl2sql_semantic/subsidence` | `configs/nl2sql_semantic/boiler` |
| 维度/范围字段 | 行政区、站点、监测类（GNSS…） | 机组/锅炉、受热面、管排、排、管 |
| `NL2SQL_SCHEMA_ALLOWLIST` / 表白名单 | `t_data_gnss` 等 | `account_boiler`、`monitor_hotarea_temp` 等 |
| RAG `nl2sql_*` 知识 | 地降库表与口径 | 锅炉库表与口径 |
| DB 连接 | 地降只读库 | 锅炉业务库 |

**不需要**再设 `NL2SQL_DOMAIN_PROFILE=boiler|subsidence` 在运行时二选一走「旧路径/新路径」。若锅炉继续使用，也应按改造后模式：**配好锅炉语义资产 + 白名单**，走同一套语义→链接→生成。

迁移期可用总开关 `NL2SQL_SEMANTIC_LINK_ENABLED` 做整链启停（灰度），这是发布保险，**不是**业务域分流。

### 3.3 与调用方的分工

| 角色 | 职责 |
|------|------|
| **基座** | 语义对齐、Schema 链接、生成 SQL、改写、校验、执行；输出 `parsed_intent` + 链接/语义元数据 |
| **对话查数** | 消费 `link_failed` / `semantic_ambiguous` 等机读信号，决定是否向用户追问后重试（**基座不等人**） |
| **报告/分析** | 任务参数预置时间/区域/站点/指标；不足则跳过段落或 degrade，禁止默认澄清挂起 |
| **HITL** | 继续用现网 `confirmed_scope` 覆盖范围；时间仍走规则/`time_intent_text` |

---

## 4. 语义建模（详细设计）

### 4.1 定义

语义建模 = 建设并在运行时使用 **业务语义层资产**，把问句中的业务说法对齐到稳定标识（`metric_id`、维度码等），**不是**再训练一个大模型。

### 4.2 资产结构（建议落地形态）

推荐目录（**按项目分目录**，部署时 `NL2SQL_SEMANTIC_DICT_PATH` 指向其一）：

```text
configs/nl2sql_semantic/
  subsidence/              # 地降项目资产
    manifest.yaml
    metrics.yaml
    synonyms.yaml
    dimensions/
      district.yaml
      station.yaml
      device_type.yaml
    units.yaml
  boiler/                  # 锅炉项目资产（改造后同样走语义+链接时使用）
    manifest.yaml
    metrics.yaml
    synonyms.yaml
    dimensions/
      boiler.yaml          # 机组/锅炉
      device.yaml          # 受热面等
      piperow.yaml         # 管排/排/管
    units.yaml
```

#### 4.2.1 `metrics.yaml`（示例字段）

```yaml
version: "2026.08.1"
metrics:
  - id: cumulative_subsidence_mm
    name: 累计沉降量
    synonyms: [沉降量, 累计沉降, 地面沉降量]
    unit: mm
    grain: station_day          # 统计粒度约定
    formula_note: "以业务确认文档为准；禁止与地下水降深混用"
    forbidden_confusions: [groundwater_drawdown_m]
    preferred_tables: [t_data_gnss, t_data_fcb]   # 提示链接，非强制最终唯一
    preferred_columns: []                        # 业务确认后填写
    time_column: data_time
```

#### 4.2.2 `synonyms.yaml`

- 正向：别名 → canonical `metric_id` / `device_type`  
- 负向：**禁混用**对（如 沉降量 ≠ 地下水降深），命中时写入 `warnings` 或降低置信度

#### 4.2.3 维度字典

| 字典 | 用途 | 来源建议 |
|------|------|----------|
| 行政区 | 通州/朝阳 → 标准名或区划码 | 权威导出 / Excel，后对齐库 |
| 站点 | 站名 ↔ `station_id` | **优先库内维表** |
| 设备/监测类 | GNSS、分层标、基岩标、地下水… | 对照七类表（见结构文档） |

地降结构摘录中已见监测类表（示例，**以甲方最新结构为准**）：`t_data_dxswj`、`t_data_fcb`、`t_data_gnss`、`t_data_gq`、`t_data_jyb`、`t_data_kxsylj`、`t_data_qxz` 等。正式白名单与字段语义须经业务确认，**禁止把抽取稿中的连接账号写入配置或提交仓库**。

### 4.3 运行时模块

| 模块 | 路径（建议） | 职责 |
|------|--------------|------|
| 加载器 | `app/nl2sql/semantic_layer.py` | 读 manifest、校验 schema、缓存版本 |
| 对齐器 | 同文件 `align_semantics(...)` | 问句+Intent → `SemanticBinding` |
| 配置入口 | `intent_config` / `AppConfig` | `NL2SQL_SEMANTIC_DICT_PATH`（指向当前项目资产根） |

#### 4.3.1 `SemanticBinding`（建议结构）

```text
SemanticBinding:
  semantic_version: str
  metrics: [{id, name, unit, grain, definition_ref, confidence}]
  dimensions: {
    district_codes?: [],
    station_ids?: [],
    device_types?: [],
  }
  warnings: [str]          # 歧义、禁混用触发等
  raw_spans: [...]         # 可选，便于 trace
```

写入 `parsed_intent["semantic"]`，并在 `NL2SQL_INJECT_PARSED_INTENT=true` 时注入 Prompt 块（沿用现有 `format_parsed_intent_prompt_block` 扩展）。

### 4.4 对齐算法（一期：规则优先）

1. **规范化**：去空白、简繁可选、同义词最长匹配。  
2. **指标命中**：synonyms → `metric_id`；多命中且互相 `forbidden_confusions` → `warnings` + 低置信。  
3. **监测类命中**：问句中的 GNSS/分层标/地下水等 → `device_types`。  
4. **维度对齐**：行政区/站点名查字典；命中则产出标准码；未命中保留原文并 warning（交由链接阶段谨慎处理）。  
5. **单位**：指标绑定默认单位；问句显式「厘米」等按 `units.yaml` 换算或 warning。  
6. **与现网时间意图合并**：时间窗仍由 `time_intent_display` 产出；语义层只补充「业务月 vs 自然月」等 **口径注释**，不替换规则时间窗（除非后续单独立项）。

> 一期 **不强制** LLM 做语义对齐；若规则不足，可配置可选 LLM 补全，但必须经字典校验，禁止自由发明 `metric_id`。

### 4.5 与现网范围解析的关系

范围维度的差异 **写在语义资产 / 改写占位符配置里**，而不是用 profile 切换代码分支：

| 维度角色（抽象） | 地降资产示例 | 锅炉资产示例 |
|------------------|--------------|--------------|
| 主实体 | 站点 `station_id` | 锅炉 `boiler` / `@unit_keyword` |
| 设备/主题 | 监测类 GNSS/分层标… | 受热面 `device_name` |
| 细粒度定位 | （按需） | 管排 / 排 / 管 |
| HITL | `confirmed_scope` / `structured_filters` | 同左（字段名随资产） |

现网锅炉规则解析（`scope_parser_rule` 等）可逐步 **收编为 boiler 语义资产 + 规则实现插件**，或一期仍作为锅炉资产下的对齐实现；目标形态是「同一 SemanticAlign / SchemaLink 接口，不同字典与白名单」。

---

## 5. 显式 Schema 链接（详细设计）

### 5.1 定义

显式 Schema 链接 = 在 LLM 生成 SQL **之前**，产出结构化的 **`LinkedSchema`**，并以此 **收窄** 进入 Prompt 的表列目录与校验白名单，使模型在「已钉住的对象集合」内生成。

区别于现网：

| | 现网 | 本改造 |
|--|------|--------|
| 链接方式 | 隐式（全量/表白名单 catalog + RAG 片段） | **显式**先链接再生成 |
| 失败形态 | 生成后再被 Validator 拒绝 | 链接阶段即可 `refuse` 或降级 |
| 可审阅性 | 难回答「为何选这张表」 | `LinkedSchema` 进入 trace |

### 5.2 `LinkedSchema`（建议结构）

```text
LinkedSchema:
  tables: [ {name, reason, score} ]
  columns: [ {table, column, role: measure|time|dim|filter, reason} ]
  joins: [ {left_table.col, right_table.col, reason} ]
  suggested_filters: [ {table.col, op, value_from: semantic|intent} ]
  catalog_fingerprint: str
  confidence: float
  status: ok | weak | failed
  fail_reason: str | null
  semantic_version: str
  allowlist_version: str
```

### 5.3 链接输入

1. `SemanticBinding`（指标偏好表列、维度码）  
2. `QuestionIntent`（时间窗、范围/HITL）  
3. `SchemaMetadataService` 反射目录（权威物理表列）  
4. **预登记白名单**（地降允许 NL2SQL 使用的表/视图集合）  
5. 可选：`NL2SQLRAGService` 检索片段（辅助打分，**不得**单独引入白名单外表）

### 5.4 链接算法（一期）

```text
A. 候选表
   - 由 metric.preferred_tables ∪ device_type→表映射 ∪ RAG 提示
   - ∩ allowlist ∩ 反射存在表
   - 打分排序，取 Top-K（K 可配，建议 1～3）

B. 候选列
   - 度量列：metric.preferred_columns 或语义角色标注列
   - 时间列：metric.time_column 或表级默认 data_time
   - 维度列：station_id / station_name / 行政区字段等
   - 全部 ∩ 反射列 ∩ 列级允许清单（若有）

C. JOIN
   - 优先反射 FK
   - 其次 allowlist 内人工 JOIN 白名单（类 ANALYSIS_NL2SQL_JOIN_WHITELIST）
   - 禁止臆造关联

D. 过滤建议
   - 时间：交给现网 _rewrite_query_filters（链接只标注时间列）
   - 站点/行政区：写入 suggested_filters，供 Prompt 与可选程序改写

E. 失败判定
   - 无候选表 / 度量列无法落库 / 强制维度全无对齐且策略要求严格
   → status=failed
```

### 5.5 Catalog 注入策略

| 模式 | 行为 | 配置建议 |
|------|------|----------|
| `linked_only`（推荐） | Prompt catalog **仅** LinkedSchema 内表列 | 改造后默认 |
| `linked_prefer` | 链接结果置顶，附加少量相关表 | 迁移过渡期 |
| `legacy_wide` | 改造前宽 catalog | 仅总开关关闭或排障临时使用 |

实现落点：复用 `_format_enriched_schema_catalog`，增加「按 LinkedSchema 过滤 `catalog_tables`」分支；`allowed_tables` / `allowed_columns` 校验集合与链接结果对齐，避免「Prompt 窄、校验宽」或相反。

### 5.6 失败策略

| `on_link_failure` | 行为 |
|-------------------|------|
| `refuse` | 不调用 LLM；响应带机读状态（如 `gen_fail_reason=link_failed`）与 `fail_reason` |
| `best_effort` | 降级 `legacy_wide` 或 `linked_prefer`，打点告警，继续现网生成 |

请求级参数优先于全局默认；**禁止**在基座内等待用户输入。

---

## 6. 模块与代码改造清单

| 模块 | 动作 | 说明 |
|------|------|------|
| 新建 `semantic_layer.py` | 新增 | 资产加载、对齐、版本校验 |
| 新建 `schema_linker.py` | 新增 | `_link_schema` / `LinkedSchema` |
| 新建 `configs/nl2sql_semantic/{subsidence\|boiler}/*` | 新增 | 按项目语义资产（不含密钥） |
| `question_scope_models.py` / intent 展示 | 扩展 | 挂载 semantic / linked_schema 摘要到 `parsed_intent` |
| `question_intent.py` | 轻量扩展 | 调用语义对齐入口（或由 chain 显式调用） |
| `chain.py` | **前半段扩展** | 意图后插入 SemanticAlign → SchemaLink；catalog 收窄；**后半段原则上不动** |
| `prompt_builder.py` / intent display | 扩展 | 注入语义口径 + 链接摘要 |
| `schema_service.py` | 复用 | 当前项目库反射；注意方言（见 §8） |
| `rag_service.py` | 复用 | 链接辅助检索；namespace 仍用 nl2sql_* |
| `scope_sql_rewrite.py` / chain 改写 | 扩展 | 占位符随资产维度扩展（锅炉保留，地增站点等） |
| `sql_cache.py` | 适配 | cache key 纳入 `semantic_version`、`allowlist` 指纹、链接模式、资产路径指纹 |
| `models/nl2sql.py` | 扩展 | `on_link_failure`；响应中语义/链接元数据（**不含** domain_profile） |
| `NL2SQLService` | 扩展 | 链接 `refuse` 短路；不实现多轮等待 |
| `validator.py` / `executor.py` | 小改或配置 | 与收窄白名单一致；LIMIT/超时沿用/加强 |
| `.env.example` | 文档化 | 语义路径、白名单、链接总开关说明 |
| 单测 | 新增 | 语义对齐、链接收窄、锅炉/地降资产夹具、refuse/best_effort |

**明确不改（一期）**：Executor 执行协议内核、L1 时间骨架算法主体、客服/分析编排图（仅文档约定消费方式）。

---

## 7. 配置与灰度

| 配置项（建议名） | 含义 | 建议默认 |
|------------------|------|----------|
| `NL2SQL_SEMANTIC_LINK_ENABLED` | 是否启用语义+链接插管（迁移总开关） | 新部署 `true`；未配齐资产前可 `false` |
| `NL2SQL_SEMANTIC_DICT_PATH` | **当前项目**语义资产根目录 | 地降→`…/subsidence`；锅炉→`…/boiler` |
| `NL2SQL_SCHEMA_ALLOWLIST` | 预登记表/视图（CSV 或文件） | 与当前项目库一致 |
| `NL2SQL_SCHEMA_LINK_CATALOG_MODE` | `linked_only` \| `linked_prefer` \| `legacy_wide` | 正式环境 `linked_only` |
| `NL2SQL_ON_LINK_FAILURE` | `refuse` \| `best_effort` | 对话倾向 `refuse`；报告可 `best_effort` |
| `NL2SQL_INJECT_PARSED_INTENT` | 注入 Prompt | 建议 `true` |
| `ANALYSIS_NL2SQL_TABLE_SCOPE_*` | 与 allowlist 对齐 | 按项目配置 |

> **已取消**：`NL2SQL_DOMAIN_PROFILE`。项目差异只通过 `SEMANTIC_DICT_PATH` + 白名单 + DB/RAG 体现。

**灰度原则**：

1. 资产未就绪时：`SEMANTIC_LINK_ENABLED=false` 整链回退改造前行为（发布保险）。  
2. 资产就绪后：地降/锅炉均 `true`，各自指向自己的字典与白名单。  
3. 缓存键必须含语义版本、allowlist 指纹、catalog mode（及资产路径指纹），防止跨项目脏命中。

---

## 8. 地降域工程化前置（与 ②③ 并行，但属准出条件）

> 下列不是「算法创新」，但是 **没有则 ②③ 无法验收**。

| 项 | 说明 |
|----|------|
| 业务库连接 | `DB_*` / `DB_URL` 指向地降只读库；**勿将明文密码写入文档或 git** |
| 方言差异 | 结构材料多为 **PostgreSQL**；现网 NL2SQL Prompt/改写偏 **TiDB/MySQL**。联调前必须确认目标方言：若地降为 PG，需单独评估 INTERVAL/函数/改写规则（并行技术风险项） |
| 表白名单 | 先圈定「允许 NL2SQL 使用的核心表/视图」，勿按全库开放 |
| RAG 摄入 | `nl2sql_schema` / `biz` / `qa` 换成地降内容；QA 样例对自由问数增益大 |
| 时间字段 | 统一 `data_time`（或表级映射写入语义资产） |
| 结构文档 | 以甲方最新说明 + 反射为准；仓库内 `_extracted_schema.txt` 仅作启动参考 |

---

## 9. 分期实施计划

### 9.1 M0 — 资产与基线（约 3～5 人日，视甲方材料）

| 工作 | 产出 |
|------|------|
| 盘点结构文档 vs 反射差异表 | 差异报告 |
| 圈定 allowlist（核心表） | `NL2SQL_SCHEMA_ALLOWLIST` 初稿 |
| 起草 metrics/synonyms/dimensions 初稿 | `configs/nl2sql_semantic/subsidence/*` |
| 收集黄金集 ≥30 条 | 问句 + 期望表列/口径 |
| 跑通现网 baseline 评测 | 基线分数 |

**准入**：有结构文档（或可反射）；有白名单初稿；有指标口径责任人。

### 9.2 M1 — 语义层可运行（约 5～8 人日）

| 工作 | 落点 |
|------|------|
| `semantic_layer.py` 加载与校验 | 新模块 |
| `align_semantics` 规则对齐 | 写入 `parsed_intent.semantic` |
| Prompt 注入语义块 | intent display |
| `NL2SQL_SEMANTIC_DICT_PATH` 指向当前项目资产 | env |
| 单测：同义词、禁混用、版本号 | tests/ |

**验收**：给定问句可稳定产出 `metric_id`/维度警告；总开关关闭时可回退改造前行为。

### 9.3 M2 — Schema 链接 + catalog 收窄（约 8～12 人日）

| 工作 | 落点 |
|------|------|
| `schema_linker.py` | LinkedSchema |
| chain 前插管 + catalog 模式 | `chain.py` |
| `refuse` / `best_effort` | Service + models |
| 校验白名单与链接对齐 | validator 输入 |
| cache key 指纹 | sql_cache |
| trace：语义版本 + LinkedSchema | 日志 / parsed_intent |
| 黄金集对比 baseline | 评测报告 |

**验收**：见 §2.2；白名单外表不可执行；链接失败机读可测。

### 9.4 M3 — 地降改写增强与加固（约 3～6 人日，可按需）

| 工作 | 说明 |
|------|------|
| 站点/行政区程序改写占位符 | 减少仅靠 Prompt 过滤 |
| JOIN 白名单补齐 | 多表问法 |
| 坏例回流 QA | 强化 `nl2sql_qa_examples` |
| 方言专项（若目标为 PG） | 改写与 Prompt 方言开关 |

---

## 10. 请求/响应契约（建议）

### 10.1 请求增量（可选字段）

```text
on_link_failure: "refuse" | "best_effort" | null
structured_filters: { ... }  # 可选已确认约束；字段随项目资产（站点/行政区 或 锅炉/受热面等）
```

> 项目选用哪套语义/白名单由**部署环境变量**决定，一般不在单次请求里用 domain_profile 切换。

现有字段继续有效：`time_intent_text`、`confirmed_scope`、`scope_intent_text`、`original_query`、`analysis_type`、`plan_item_id` 等。

### 10.2 响应增量（建议）

```text
parsed_intent:
  ...现有 time/scope...
  semantic: { version, metrics, dimensions, warnings }
  linked_schema: { tables, columns, joins, status, fail_reason, confidence }

# 链接拒绝时（示例）
gen_fail_reason: link_failed | semantic_ambiguous | ...
sql: ""
rows: []
```

调用方据此决定：对话追问、报告跳过段落、或改参重试。

---

## 11. 测试与验收清单

### 11.1 单测

- [ ] 同义词最长匹配与禁混用 warning  
- [ ] 语义资产缺文件 / 版本非法时的失败模式  
- [ ] 链接结果 ∩ allowlist；白名单外表永不进入 catalog（`linked_only`）  
- [ ] `refuse` 不调用 LLM（mock）  
- [ ] `best_effort` 降级路径可生成  
- [ ] `SEMANTIC_LINK_ENABLED=false` 时与改造前基线一致；开启后锅炉资产夹具可跑通  

### 11.2 集成 / 黄金集

- [ ] 地降只读库联调：反射表与资产 preferred_tables 一致  
- [ ] 黄金集对比报告（表列正确率、可执行率、口径追溯）  
- [ ] 综合分析多槽批跑：无人工输入、无澄清挂起  
- [ ] 客服 `data_query` 烟测（若同部署）  

### 11.3 对外可演示口径

- [ ] 可展示：问句 → 语义命中（指标/站点）→ LinkedSchema → SQL → rows  
- [ ] 可说明：口径来自语义资产版本号；表列来自链接而非模型臆造  
- [ ] 可说明：不确定时返回机读失败，由产品层决定澄清或改参——**基座不阻塞**

---

## 12. 风险与对策

| 风险 | 影响 | 对策 |
|------|------|------|
| 指标口径无法业务确认 | 语义层空洞 | M1 只上同义词+监测类；口径未确认的指标不进开放集 |
| 结构文档与库不一致 | 链接系统性错误 | 以反射为准出差异表，甲方确认 |
| 链接过严 → 零 SQL | 可用性下降 | 过渡 `best_effort` + `linked_prefer`；放宽 Top-K |
| 链接链错 | 错得更自信 | 黄金集门禁；trace 审阅；坏例回流 |
| PostgreSQL vs MySQL 方言 | 改写/Prompt 失效 | §8 单列风险；确认目标库后专项 |
| 语义资产把密钥写进仓库 | 安全事故 | 资产仅结构与口径；连接只走环境变量 |
| 锅炉与地降字段互相污染 | 错链/错改写 | 部署级隔离资产路径与白名单；禁止混用同一 SEMANTIC_DICT_PATH |
| 误把查询类型/澄清做大 | 工期偏离准度目标 | **范围冻结为本方案 G1–G6** |

---

## 13. 立即行动项

1. **冻结范围**：本期只做语义建模 + 显式 Schema 链接 + 地降域工程化前置；五阶段①⑤不纳入一期。  
2. **向甲方索取**：最新表结构、只读账号、核心表白名单、指标口径确认人、行政区/站点权威数据。  
3. **内部启动 M0**：差异表 + allowlist 初稿 + 语义 YAML 骨架 + 黄金集收集。  
4. **确认目标 SQL 方言**（PG / MySQL / TiDB），写入联调检查单。  
5. **定部署配置包**：地降指向 `subsidence` 资产；若锅炉项目升级，另备 `boiler` 资产与白名单，**同一管线、不同配置**。  

---

## 14. 文档与代码索引

| 文档/代码 | 用途 |
|-----------|------|
| `enterprise-level_transformation_docs/企业级NL2SQL实现方案.md` | 现网基座全链路 |
| `docs/基于地降所项目改造/NL2SQL基座五阶段改造方案.md` | 五阶段背景与①④⑤讨论 |
| `docs/NL2SQL自然语言时间和范围窗口解析&改写改造落地方案.md` | 时间/范围改写（保留并扩展） |
| `app/nl2sql/chain.py` | 插管主挂载点 |
| `app/nl2sql/question_intent.py` | 现网意图入口 |
| `app/nl2sql/schema_service.py` | 反射 |
| `app/nl2sql/rag_service.py` | 三命名空间 RAG |
| `configs/prompts.yaml` · `nl2sql` | SQL 生成提示词（地降需改业务提示，catalog 由链接收窄） |

---

## 15. 附录：建议的地降监测类 → 表映射草稿（待业务确认）

> 来源于结构摘录的**启动草稿**，正式以甲方确认 + 反射为准。

| 监测类（device_type） | 表示例 | 时间字段（摘录） |
|----------------------|--------|------------------|
| 地下水 | `t_data_dxswj` | `data_time` |
| 分层标 | `t_data_fcb` | `data_time` |
| GNSS | `t_data_gnss` | `data_time` |
| 光纤 | `t_data_gq` | `data_time` |
| 基岩标 | `t_data_jyb` | `data_time` |
| 孔隙水 | `t_data_kxsylj` | `data_time` |
| 气象站 | `t_data_qxz` | `data_time` |

指标 → 表/列的 `preferred_*` **必须**业务签字后再进入 `metrics.yaml` 开放集。

---

*本文是地降 NL2SQL 基座一期落地的范围说明书。若需扩展查询类型意图或结果图表契约，另开文档，避免稀释 ②③ 的准确率目标。*
