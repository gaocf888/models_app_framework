# NL2SQL 基座改造方案（锅炉四管 / 地面沉降）

> **版本**：2026-08-24（合并语义+链接落地方案，**本文为唯一维护文档**）  
> **实施进度**：P0–P5 主能力已落地；**配置包驱动分业务默认值（含 DB 连接默认、方言、白名单、Prompt）已补齐**。`.env` 中显式 `DB_*`/`NL2SQL_*` 仍优先于 profile。剩余联调：PG 连通、RAG 摄入、黄金集可执行率、锅炉回归。运维极简见企业级简版 §4.4。  
> **分支/项目**：`dev_djs`（地降所地面沉降）  
> **范围**：`app/nl2sql/*`、`NL2SQLService`、相关配置与契约；不含 chatbot/报告前端 UI 实现细节。  
> **原则**：**基座主链路不变**；范围默认 **rule**；时间 **始终规则**；通过 **部署级全局业务配置** 区分锅炉四管与地面沉降；语义建模 + 显式 Schema 链接为准确率核心增量。  
> **决策依据**：相对现网「RAG + 反射 + 强后处理」，对 NL→SQL 准确率增益最大的是 **语义建模** 与 **显式 Schema 链接**；查询类型五阶段、基座内多轮澄清、图表引擎等 **一期不做**。  
> **域差异原则**：**不设** `NL2SQL_DOMAIN_PROFILE` 运行时双管线分流；锅炉/地降共用同一套「语义→链接→现网后半段」，差异仅在 `configs/nl2sql_business/<domain>/` 配置包。  
> **关联文档**：  
> - 现网基线：`enterprise-level_transformation_docs/企业级NL2SQL实现方案.md`  
> - 五阶段背景：`NL2SQL基座五阶段改造方案.md`（或废弃提炼稿）  
> - 时间/范围改写：`docs/NL2SQL自然语言时间和范围窗口解析&改写改造落地方案.md`  
> - 库结构：`docs/地降所需求及数据相关/数据库结构及逻辑/数据库说明.md`、`226大模型数据库.docx`  
> - 需求参考（**非数据真源**）：`docs/地降所需求及数据相关/需求梳理/智能数据查询及分析-需求剖析.md`

---

## 0. 改造总览

### 0.1 三条改造主线（与你整理的方案对齐）

| 主线 | 内容 | 本文章节 |
|------|------|----------|
| **① 基座适配 + 全局业务配置** | 换库/白名单/RAG/Prompt/词表等；`NL2SQL_BUSINESS_DOMAIN` + `configs/nl2sql_business/` | §2、§3、§7 |
| **② 业务侧覆盖范围** | `confirmed_scope` → `human_confirmed`（**已具备，保持**）；时间用 `time_intent_text` | §4 |
| **③ 语义建模 + Schema 链接** | 意图后插管；地降 8 表事实模型；指标/链接资产 | §5、§6 |

### 0.2 基座主链路（改造后仍保持）

```text
NL2SQLQueryRequest
        │
        ▼
resolve_question_intent          # 时间：规则；范围：默认 rule / confirmed_scope
        │
        ▼
★ SemanticAlign（语义建模）     # 新增；subsidence 资产驱动
        │
        ▼
★ SchemaLink → LinkedSchema     # 新增；8 表 + JOIN/聚合
        │
        ▼
Schema 反射 →（可选）规划 → RAG/QA/L2/L1 缓存
        │
        ▼
Prompt（catalog 来自 LinkedSchema）→ LLM → normalize
        │
        ▼
时间·范围 SQL 改写（PG 方言）→ 校验 → EXPLAIN/execute → refine
        │
        ▼
NL2SQLQueryResponse（sql, rows, parsed_intent…）
```

**冻结不重写**：`SQLExecutor` 内核、L1 时间骨架算法主体、客服/分析编排图（仅约定如何传参）。

**主代码挂载点**：`NL2SQLChain.generate_sql_with_validation_context`（`app/nl2sql/chain.py`），位于 `resolve_question_intent` 成功之后、组装 `full_catalog` / 调用 LLM 之前。

### 0.3 改造边界（做 / 不做）

| 做（一期） | 不做（一期明确排除） |
|------------|----------------------|
| 版本化语义资产 + 运行时对齐（各 domain 各配一套） | 完整「查询类型」五阶段意图体系 |
| `_link_schema` → `LinkedSchema` + catalog 收窄 | 基座内多轮澄清 / 等人回话 |
| 统一管线；锅炉/地降仅换 `nl2sql_business` 配置包 | **`NL2SQL_DOMAIN_PROFILE` 域运行时分流** |
| 表白名单、维度改写、trace 可审阅 | 重写 Validator / Executor / 缓存内核 |
| 总开关灰度 `NL2SQL_SEMANTIC_LINK_ENABLED` | 基座内图表引擎 / BI 前端；一次性语义化全库 |

### 0.4 为何聚焦语义 + 链接

| 能力 | 现网状态 | 对准确率 | 本期态度 |
|------|----------|----------|----------|
| 时间窗 / 范围改写 | 已较强（规则） | 已覆盖一大类问题 | **保留**；地降扩行政区/站点槽位 |
| QA 回放 / L1·L2 缓存 / refine | 已较强 | 已知问句很稳 | **保留** |
| 隐式 Schema（反射+RAG+白名单） | 有，但白名单内仍易选错表列 | 自由问数仍易错 | **升级为显式链接** |
| 业务口径（指标/单位/码表） | 散落 Prompt / biz RAG | **最大短板** | **语义层补齐** |
| 查询类型 + refuse | 基本没有 | 偏可控性，非准度主杠杆 | **延后** |

地降自由问数典型错误：「沉降量」与地下水混用 → **语义**；问 GNSS 却扫分层标表 → **链接**；行政区模糊 LIKE → **语义码表 + 链接过滤**。

### 0.5 目标与量化成功标准

| 编号 | 目标 |
|------|------|
| G1 | 可版本化业务语义层（指标/同义词/单位/维度），写入 `parsed_intent`（含口径引用与语义版本号） |
| G2 | 显式 `LinkedSchema`：候选表/列/JOIN/过滤 ∩ DB 反射 ∩ 预登记白名单 |
| G3 | Prompt catalog **默认**使用链接结果（`linked_only`）；可配置降级宽 catalog |
| G4 | **统一模式**：锅炉与地降共用语义→链接→现网后半段；差异只在配置包 |
| G5 | 链接/语义失败返回 **机读原因**；`refuse` / `best_effort` 可配置；**基座不等人** |
| G6 | 现网后半段保持；锅炉通过 **boiler_four_tube 配置包** 接入，非 profile 退回旧路径 |

在同一地降黄金集（建议 ≥30 条自由问数）上对比 **baseline（仅现网）** vs **语义+链接开启**：

| 指标 | 期望 |
|------|------|
| 主表选对率 | 相对 baseline **明显提升**（目标 +15pt 起，以实测为准） |
| 关键度量列选对率 | 相对 baseline 提升 |
| 可执行率（校验通过且 SELECT 成功） | 不低于 baseline |
| 口径可追溯率 | 命中语义条目的问句覆盖登记核心指标 |
| 锅炉黄金集（配锅炉资产后） | 表列/口径不低于改造前基线 |

### 0.6 已确认的业务与数据原则（澄清汇总）

| # | 原则 | 落地要求 |
|---|------|----------|
| C1 | **以物理库为准**，不以原型 UI 为准 | 不做「年滑速」等原型字段；指标仅来自库列与业务口径 |
| C2 | 事实表前缀 **`t_data_wash_*`** + **`t_station`** | 共 **8 张表** 进入 NL2SQL 白名单 |
| C3 | 库连接以 **226 事实库**为准 | 准生产；文档中连接已验证（只读 `postgres`） |
| C4 | NL2SQL 只查 **8 张表** | 无汇总视图/宽表；聚合在 SQL 层完成 |
| C5 | **JOIN 允许** | 跨表问句、季报等辅助数据 JOIN |
| C6 | RAG 三命名空间 **共用** | 同一 `nl2sql_schema/biz/qa`；靠 **摄入内容** 区分业务 |
| C7 | **一套部署一个 domain** | `NL2SQL_BUSINESS_DOMAIN=boiler_four_tube \| subsidence` |
| C8 | 范围默认 **rule**；时间 **规则** | `NL2SQL_INTENT_PARSE_MODE=rule` |
| C9 | 沉降主数据：**分层标 `t_data_wash_fcb`**（兼基岩标 `jyb`） | 泛化「监测点沉降」默认主表；气象/水位等为辅助 |
| C10 | 行政区通过 **`t_station`** | `事实表.project_name = t_station.name` → `t_station.area` |

---

## 1. 地面沉降数据模型（NL2SQL 真源）

### 1.1 数据库连接（subsidence 部署）

| 项 | 值（以 `数据库说明.md` / `226大模型数据库.docx` 为准） |
|----|--------------------------------------------------------|
| 主机 | `192.169.237.197`（文档亦记 172.16.66.226，**以联调可用地址为准**） |
| 端口 | `5432` |
| 数据库 | `dmcj` |
| 用户 | `postgres`（只读） |
| 方言 | **PostgreSQL**（非现网默认 TiDB/MySQL） |

环境变量示例（`.env`，**密码勿写入仓库**）：

```bash
DB_HOST=192.169.237.197
DB_PORT=5432
DB_NAME=dmcj
DB_USER=postgres
DB_PASSWORD=<from_secret>
# 或 DB_URL=postgresql+psycopg2://...
NL2SQL_SQL_DIALECT=postgres
NL2SQL_BUSINESS_DOMAIN=subsidence
```

### 1.2 八张 NL2SQL 对象清单

| 表名 | 业务含义 | 角色 | 主要度量/特征列 |
|------|----------|------|-----------------|
| `t_data_wash_fcb` | 分层标（清洗） | **主表**（沉降主分析） | `total_settle`, `data_time`, `station_id`, `station_name`, `project_name` |
| `t_data_wash_jyb` | 基岩标（清洗） | **主表**（与分层标并列主数据） | `total_settle`, `data_time`, … |
| `t_data_wash_gnss` | GNSS | 辅助 / 位移专题 | `displacement_2d`, `displacement_3d`, `gps_total_*`, `data_time` |
| `t_data_wash_dxswj` | 地下水井 | 辅助 | `deep`, `elevation`, `data_time` |
| `t_data_wash_kxsylj` | 孔隙水压力 | 辅助 | `pressure`, `data_time` |
| `t_data_wash_gq` | 光纤 | 辅助 | `total_settle`, `data_time` |
| `t_data_wash_qxz` | 气象站 | 辅助 | `temp`, `humidity`, `pressure`, `wind_*`, `real_time_rain`, `data_time` |
| `t_station` | 监测站点维表 | **范围/行政区** | `name`, `code`, `area`, `lon`, `lat` |

**监测类型与表映射（语义层 `device_type`）**：

| 监测类型（问句） | 默认表 | 备注 |
|------------------|--------|------|
| 分层标 / 分层 | `t_data_wash_fcb` | 当前仅有最上层清洗数据；**分层各层表预留占位**（§5.8） |
| 基岩标 | `t_data_wash_jyb` | |
| GNSS | `t_data_wash_gnss` | |
| 地下水 / 水位井 | `t_data_wash_dxswj` | |
| 孔隙水 | `t_data_wash_kxsylj` | |
| 光纤 | `t_data_wash_gq` | |
| 气象 | `t_data_wash_qxz` | |
| 泛化「沉降 / 监测点」 | **`t_data_wash_fcb`**（默认） | 未指明类型时 |

### 1.3 关联与范围（已确认）

```text
范围维度：行政区划 + 监测站点

行政区：
  各事实表.project_name  =  t_station.name
  → 过滤/展示用 t_station.area（行政区）

站点：
  事实表.station_id / station_name
  或 t_station.code / t_station.name

时间：
  统一过滤列 → 各事实表.data_time（语义资产中标注）
  周期沉降/回弹 → 起止 data_time 窗口内 total_settle 差值（见 §5.6 指标）
```

**标准 JOIN（写入 JOIN 白名单与 Prompt）**：

```sql
-- 事实表别名 d，站点维表 s
d.project_name = s.name
-- 或按 station_id 关联（若库内 id 体系一致，反射确认后补充）
```

### 1.4 与原型 UI 的差异（明确不做）

| 原型概念 | 物理库 | NL2SQL 策略 |
|----------|--------|-------------|
| 年滑速 (mm/yr) | **无此列** | **不实现**；若问速率，用 `total_settle` 时间窗差值 / 天数（指标 `period_subsidence_mm`） |
| 岩性过滤器 | **无此列** | 一期不做；问句出现岩性时 warning 或 best_effort |
| 16 条演示站 | 真实 `t_station` | 以维表为准 |

---

## 2. 全局业务配置（部署级）

### 2.1 设计目标

- **一个主开关** + **配置包目录**：同一套代码，锅炉/地降靠部署时选 domain。  
- **不设**运行时 `boiler` 走旧链路、`subsidence` 走新链路的双管线；**改造后两业务均走「语义 + 链接 + 现网后半段」**（锅炉需补齐 `boiler_four_tube` 资产）。  
- **迁移总开关**：`NL2SQL_SEMANTIC_LINK_ENABLED`（资产未齐可整链关闭，回退改造前行为）。

### 2.2 主开关

```bash
# 部署级（一套进程只设一个）
NL2SQL_BUSINESS_DOMAIN=boiler_four_tube   # 锅炉四管
NL2SQL_BUSINESS_DOMAIN=subsidence        # 地面沉降
```

### 2.3 配置包目录结构

```text
configs/nl2sql_business/
  boiler_four_tube/
    profile.yaml                 # 本业务 NL2SQL 默认值汇总
    table_scope.txt              # ANALYSIS_NL2SQL_TABLE_SCOPE 等价
    join_whitelist.txt           # 人工 JOIN 补充
    scope_lexicon.json           # 机组/受热面/管排…
    entity_rules.json
    semantic/
      manifest.yaml
      metrics.yaml
      synonyms.yaml
      dimensions/
        boiler.yaml
        device.yaml
        piperow.yaml
      units.yaml
    prompts/
      nl2sql_prompt_snippet.md   # 或指向 prompts.yaml 的 version 名
  subsidence/
    profile.yaml
    table_scope.txt              # 8 表
    join_whitelist.txt           # project_name=name 等
    scope_lexicon.json           # 行政区/站点/监测类型
    entity_rules.json
    semantic/
      manifest.yaml
      metrics.yaml
      synonyms.yaml
      dimensions/
        district.yaml            # 来自 t_station.area 导出或规则
        station.yaml             # t_station
        device_type.yaml
      units.yaml
      layered_fcb_placeholder.yaml   # 分层各层预留
```

### 2.4 `profile.yaml` 字段规范（subsidence 示例）

```yaml
business_domain: subsidence
display_name: 北京市地面沉降监测

db:
  dialect: postgres
  # host/port/name/user 写入 profile 作为部署默认；密码禁止写入仓库，仅 DB_PASSWORD / DB_URL
  # 显式环境变量 DB_* / DB_URL 优先于此处
  host: 192.169.237.197
  port: 5432
  name: dmcj
  user: postgres
  async_driver: postgresql+asyncpg

nl2sql:
  semantic_link_enabled: true
  semantic_dict_path: configs/nl2sql_business/subsidence/semantic
  intent_parse_mode: rule
  scope_sql_rewrite_enabled: true
  scope_lexicon_file: configs/nl2sql_business/subsidence/scope_lexicon.json
  entity_rules_file: configs/nl2sql_business/subsidence/entity_rules.json
  prompt_default_version: v2_subsidence   # 见 §7.3
  schema_link_catalog_mode: linked_only
  on_link_failure: best_effort          # 报告场景；对话可请求级 refuse
  inject_parsed_intent: true
  reject_unresolved_time_placeholders: true

tables:
  allowlist_file: configs/nl2sql_business/subsidence/table_scope.txt
  join_whitelist_file: configs/nl2sql_business/subsidence/join_whitelist.txt

rag:
  # 共用 namespace，靠摄入内容区分
  namespaces:
    schema: nl2sql_schema
    biz: nl2sql_biz_knowledge
    qa: nl2sql_qa_examples

analysis:
  # 地降 QA/计划 五类报告（先按此实现 analysis_type）
  report_types:
    - subsidence_daily
    - subsidence_weekly
    - subsidence_monthly
    - subsidence_quarterly
    - subsidence_yearly
```

### 2.5 配置加载优先级

```text
显式环境变量 NL2SQL_* / DB_* / ANALYSIS_NL2SQL_*
    ＞  profile.yaml（由 NL2SQL_BUSINESS_DOMAIN 选定）
    ＞ 代码默认值（现网锅炉默认）
```

**实现落点（规划）**：`app/core/config.py` 增加 `load_nl2sql_business_profile()`；`intent_config`、`chain._resolve_table_scope`、`scope_lexicon` 默认路径从 profile 读取。

语义资产路径：优先 `profile.yaml` 内 `nl2sql.semantic_dict_path`（如 `configs/nl2sql_business/subsidence/semantic`）；亦可单独设 `NL2SQL_SEMANTIC_DICT_PATH` 覆盖。**不再**使用独立的 `configs/nl2sql_semantic/` 根目录（资产统一收编进 `nl2sql_business` 包）。

### 2.6 语义+链接相关环境变量（与 profile 对齐）

| 配置项 | 含义 | subsidence 建议 | 锅炉建议 |
|--------|------|-----------------|----------|
| `NL2SQL_BUSINESS_DOMAIN` | 部署级 domain | `subsidence` | `boiler_four_tube` |
| `NL2SQL_SEMANTIC_LINK_ENABLED` | 语义+链接插管总开关 | 资产齐后 `true` | 同左 |
| `NL2SQL_SEMANTIC_DICT_PATH` | 语义资产根（可选覆盖 profile） | `…/subsidence/semantic` | `…/boiler_four_tube/semantic` |
| `NL2SQL_SCHEMA_LINK_CATALOG_MODE` | `linked_only` \| `linked_prefer` \| `legacy_wide` | `linked_only` | `linked_only` |
| `NL2SQL_ON_LINK_FAILURE` | `refuse` \| `best_effort` | 对话 `refuse`；报告可 `best_effort` | 同左 |
| `NL2SQL_INJECT_PARSED_INTENT` | 注入 Prompt | `true` | `true` |

> **已取消**：`NL2SQL_DOMAIN_PROFILE`。项目差异只通过 `NL2SQL_BUSINESS_DOMAIN` + 配置包 + DB/RAG 体现。

**灰度原则**：

1. 资产未就绪：`SEMANTIC_LINK_ENABLED=false` 整链回退改造前行为。  
2. 资产就绪后：各 domain 各自指向自己的字典与白名单，均 `true`。  
3. 缓存键须含 `semantic_version`、allowlist 指纹、catalog mode、domain 指纹，防止跨项目脏命中。

### 2.7 `table_scope.txt`（subsidence 已定稿）

```text
t_data_wash_fcb
t_data_wash_jyb
t_data_wash_gnss
t_data_wash_dxswj
t_data_wash_kxsylj
t_data_wash_gq
t_data_wash_qxz
t_station
```

---

## 3. 改造范围①：按类目逐项说明

> 基座逻辑不变；下列为 **更换项目 / 切换 domain** 时必须调整的类目。

### 3.1 总表

| 类目 | 锅炉四管（现网） | 地面沉降（subsidence） | 改造类型 |
|------|------------------|------------------------|----------|
| DB 连接 | 业务 TiDB/MySQL 库 | PostgreSQL `dmcj` | 配置 |
| SQL 方言 | TiDB/MySQL 改写 | **PostgreSQL 分支** | **代码 + 配置** |
| 表白名单 | `ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT` 锅炉表集 | 8 表（§1.2） | 配置包 |
| JOIN 白名单 | FK + 人工白名单 | `project_name=name` + 跨事实表 | 配置包 |
| RAG 三库 | 锅炉 schema/biz/qa | **同 namespace**，地降内容摄入 | 运维摄入 |
| SQL 提示词 | `prompts.yaml` · `nl2sql` v2 锅炉段 | `v2_subsidence` 或分文件 | 配置 + YAML |
| 词表/范围 rule | `nl2sql_scope_device_aliases.json` | 行政区/站点/监测类型词表 | 配置 + **规则扩展** |
| 范围 SQL 改写 | `@unit_keyword` 等 | `@station_id`、`@district`（`t_station.area`） | **代码扩展** |
| 实体否定规则 | 锅炉 entity_rules | 地降禁混用规则 | 配置包 |
| 时间解析 | `time_intent_display` 规则 | **沿用**；核对季度/报告周期 | 规则核对 |
| 范围解析模式 | 默认 `rule` | 默认 `rule` | 保持 |
| 缓存/QA 指纹 | 锅炉 analysis_type | 五类报告 `analysis_type` | 配置 + QA 摄入 |
| RAG 检索参数 | `RAG_SCENE_NL2SQL_*` | 可沿用或微调 top_k | 可选配置 |
| 锚点回退 | img_diag 锅炉场景 | 地降不用则关闭或空 | 配置 |
| 分析 plan 模板 | `analysis_plan_overheat_*` 等 | `analysis_plan_subsidence_*` 五类 | **prompts.yaml 分业务** |
| 语义 + 链接 | 待迁到 `boiler_four_tube/semantic` | `subsidence/semantic` | **新增模块** |

### 3.2 SQL 方言（PostgreSQL）— 必做代码项

现网 `chain.py` 强依赖 TiDB/MySQL（`DATE_SUB`、`INTERVAL 7 DAY`、禁 `INTERVAL '7 days'`）。

| 项 | 改造 |
|----|------|
| 环境变量 | `NL2SQL_SQL_DIALECT=postgres` \| `tidb`（默认 tidb 兼容现网锅炉） |
| 时间窗 SQL | `time_intent_display` 产出 PG 表达式（或方言适配层转换） |
| `_rewrite_query_filters` | PG 日期字面量、`@t_*` 替换语法 |
| Prompt | `nl2sql` 模板声明 **PostgreSQL**，禁止 TiDB 专属写法 |
| Validator/Executor | 确认 SQLAlchemy 驱动与 `EXPLAIN` 在 PG 上可用 |

### 3.3 范围规则与改写（地降）

**规则解析（`scope_parser_rule` 扩展或 subsidence 插件）**：

| 槽位 | 来源 | 说明 |
|------|------|------|
| `station_id` / `station_name` | 问句 + `scope_lexicon` | 对齐 `t_station` |
| `district` / `area` | 问句中的「朝阳区」等 | 对齐 `t_station.area` |
| `device_type` | GNSS/分层标/气象… | 驱动 Schema 链接主表 |

**SQL 改写占位符（建议）**：

| 占位符 | 绑定 | 行为 |
|--------|------|------|
| `@station_id` | `station_id` | 替换为字面量或 `IN (...)` |
| `@station_keyword` | `station_name` | LIKE |
| `@district` / `@area` | 通过 JOIN `t_station` 的 `area` | 改写为 `s.area = '...'` 或子查询 |
| `@project_name` | `project_name` | 与站点场地名关联 |

锅炉侧 **保留** `@unit_keyword`、`@device_keyword` 等（`boiler_four_tube` profile 启用）。

### 3.4 实体否定规则（地降示例）

```json
[
  {
    "question_contains_any": ["沉降", "下沉", "回弹"],
    "sql_pattern": "(?i)t_data_wash_dxswj",
    "message": "问沉降主指标时不应直接查地下水表；优先 fcb/jyb 的 total_settle"
  },
  {
    "question_contains_any": ["水位", "埋深", "降深"],
    "sql_pattern": "(?i)t_data_wash_fcb",
    "message": "地下水指标应查 t_data_wash_dxswj，勿用分层标 total_settle"
  },
  {
    "question_contains_any": ["GNSS", "位移"],
    "sql_pattern": "(?i)t_data_wash_fcb(?!.*gnss)",
    "message": "GNSS 位移应查 t_data_wash_gnss 的 displacement_3d/2d"
  }
]
```

### 3.5 缓存 / QA 指纹

`compute_nl2sql_policy_fp` 已含表白名单、Prompt 版本、entity_rules 等；切换 profile 后自动变化。

**地降 `analysis_type`（先按五类报告）**：

| analysis_type | 含义 |
|---------------|------|
| `subsidence_daily` | 日报 |
| `subsidence_weekly` | 周报 |
| `subsidence_monthly` | 月报 |
| `subsidence_quarterly` | 季报 |
| `subsidence_yearly` | 年报 |

QA 摄入时 `doc_name` 五元组需带上述 `analysis_type` + `plan_template_version`（与现网锅炉 QA 机制一致）。

### 3.6 锚点回退

`NL2SQL_ANCHOR_FALLBACK_ANALYSIS_TYPES` 默认 `img_diag_*`（锅炉看图诊断）。地降部署建议：

```bash
NL2SQL_ANCHOR_FALLBACK_NOW_ENABLED=false
# 或 ANALYSIS_TYPES 留空
```

### 3.7 分析 plan 模板（prompts 分业务）

**策略**：`prompts.yaml` 内用 **scene 前缀或独立文件** 区分业务，由 `profile.yaml` 指定加载版本。

| 业务 | 场景示例 |
|------|----------|
| 锅炉四管 | 现有 `analysis_plan_overheat_guidance`、`analysis_plan_img_diag_*` … |
| 地面沉降 | 新增 `analysis_plan_subsidence_daily` … `analysis_plan_subsidence_yearly` |

实现选项（择一，推荐 A）：

- **A**：`configs/prompts_subsidence.yaml` 与 `configs/prompts_boiler.yaml` 合并加载，或按 `PROMPTS_CONFIG_PATH` 切换。  
- **B**：单 `prompts.yaml` 内所有 `analysis_plan_subsidence_*` 与锅炉模板并列；`PromptTemplateRegistry` 按 `NL2SQL_BUSINESS_DOMAIN` 过滤可见 scene。

---

## 4. 改造范围②：业务侧 `confirmed_scope`（保持现网）

### 4.1 已具备能力

`app/nl2sql/question_intent.py`：传入 `confirmed_scope` 时：

- 范围：**不再**走 rule/LLM 解析；
- `parse_mode = human_confirmed`；
- 时间：**仍**从 `scope_intent_text` / `time_intent_text` / `question` / `original_query` 做 **规则**解析。

### 4.2 调用约定（各业务板块）

| 调用方 | 范围 | 时间 |
|--------|------|------|
| 看图诊断 HITL | `confirmed_scope` | `scope_intent_text` / `original_query` |
| 客服 / 智能体自建 LLM 范围 | `confirmed_scope` + 可选 `structured_filters` | **`time_intent_text`** 传用户原句 |
| 综合分析 plan | plan `question` + **`time_intent_text=用户 query`** | 已现网 |

**地降 `confirmed_scope` 建议字段**：

```json
{
  "station_id": "…",
  "station_name": "…",
  "district": "朝阳区",
  "device_type": "分层标"
}
```

与锅炉字段（`boiler`, `device_name`, `piperow_name`, `row_no`, `tube_no`）**并存**；序列化时按 domain 输出相关子集。

### 4.3 未实现（一期不做）

- **`confirmed_time_window`**：时间不能通过 `confirmed_scope` 覆盖；后续可扩展基座契约。

### 4.4 与调用方的分工

| 角色 | 职责 |
|------|------|
| **基座** | 语义对齐、Schema 链接、生成 SQL、改写、校验、执行；输出 `parsed_intent` + 链接/语义元数据 |
| **对话查数** | 消费 `link_failed` / `semantic_ambiguous` 等机读信号，决定是否追问后重试（**基座不等人**） |
| **报告/分析** | 任务参数预置时间/区域/站点/指标；不足则跳过段落或 degrade，禁止默认澄清挂起 |
| **HITL** | 继续用 `confirmed_scope` 覆盖范围；时间仍走规则/`time_intent_text` |

项目选用哪套语义/白名单由 **部署环境变量 `NL2SQL_BUSINESS_DOMAIN`** 决定，一般不在单次请求里切换 domain。

---

## 5. 改造范围③：语义建模

### 5.1 定义与模块职责

语义建模 = 建设并在运行时使用 **业务语义层资产**，把问句中的业务说法对齐到稳定标识（`metric_id`、维度码等），**不是**再训练一个大模型。

- 输入：问句 + `QuestionIntent` + 当前 domain 语义资产  
- 输出：`SemanticBinding` → 写入 `parsed_intent.semantic`  
- 算法：**一期规则优先**（同义词、监测类型、站点/行政区字典），可选 LLM 补全须回字典校验  

| 模块 | 路径（建议） | 职责 |
|------|--------------|------|
| 加载器 | `app/nl2sql/semantic_layer.py` | 读 manifest、校验 schema、缓存版本 |
| 对齐器 | 同文件 `align_semantics(...)` | 问句+Intent → `SemanticBinding` |
| 配置入口 | `intent_config` / profile | `semantic_dict_path` 或 `NL2SQL_SEMANTIC_DICT_PATH` |

**代码规划**：挂载于 `resolve_question_intent` 之后（`chain.py` 插管）。

### 5.2 资产结构（`configs/nl2sql_business/<domain>/semantic/`）

```text
semantic/
  manifest.yaml          # 版本、依赖、校验规则
  metrics.yaml           # 指标开放集
  synonyms.yaml          # 正向别名 + 负向禁混用
  dimensions/
    district.yaml        # 地降：行政区
    station.yaml         # 地降：站点
    device_type.yaml     # 监测类型
    boiler.yaml          # 锅炉：机组
    device.yaml          # 锅炉：受热面
    piperow.yaml         # 锅炉：管排
  units.yaml
  layered_fcb_placeholder.yaml   # 地降：分层各层预留
```

#### `synonyms.yaml` 要点

- 正向：别名 → canonical `metric_id` / `device_type`  
- 负向：**禁混用**对（沉降量 ≠ 地下水降深），命中时写入 `warnings` 或降低置信度

#### 维度字典来源

| 字典 | 用途 | 来源建议 |
|------|------|----------|
| 行政区 | 朝阳/通州 → 标准名 | `t_station.area` 导出或权威 Excel |
| 站点 | 站名 ↔ `station_id` | **优先 `t_station` 维表** |
| 设备/监测类 | GNSS、分层标… | 对照 §1.2 表映射 |

### 5.3 `SemanticBinding`（建议结构）

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

写入 `parsed_intent["semantic"]`；`NL2SQL_INJECT_PARSED_INTENT=true` 时经 `format_parsed_intent_prompt_block` 注入 Prompt。

### 5.4 对齐算法（一期：规则优先）

1. **规范化**：去空白、同义词最长匹配。  
2. **指标命中**：synonyms → `metric_id`；多命中且互相 `forbidden_confusions` → `warnings` + 低置信。  
3. **监测类命中**：GNSS/分层标/地下水等 → `device_types`。  
4. **维度对齐**：行政区/站点名查字典；命中产出标准码；未命中保留原文并 warning。  
5. **单位**：指标默认单位；问句显式单位按 `units.yaml` 换算或 warning。  
6. **与现网时间意图合并**：时间窗仍由 `time_intent_display` 产出；语义层只补充口径注释，**不替换**规则时间窗。

> 一期 **不强制** LLM 做语义对齐；若规则不足，可选 LLM 补全但必须经字典校验，禁止自由发明 `metric_id`。

### 5.5 与现网范围解析的关系

范围维度差异 **写在语义资产 / 改写占位符配置里**，而非 profile 切换代码分支：

| 维度角色（抽象） | 地降资产示例 | 锅炉资产示例 |
|------------------|--------------|--------------|
| 主实体 | 站点 `station_id` | 锅炉 `boiler` / `@unit_keyword` |
| 设备/主题 | 监测类 GNSS/分层标… | 受热面 `device_name` |
| 细粒度定位 | （按需） | 管排 / 排 / 管 |
| HITL | `confirmed_scope` / `structured_filters` | 同左（字段名随资产） |

现网锅炉 `scope_parser_rule` 可逐步收编为 `boiler_four_tube` 语义资产下的对齐实现；目标形态是 **同一 SemanticAlign / SchemaLink 接口，不同字典与白名单**。

### 5.6 开放指标清单（根据已确认信息制定）

> 不包含原型「年滑速」；周期类指标基于 **`total_settle` 差值**。

| metric_id | 名称 | 单位 | 主表 | 主列 | 公式/口径 |
|-----------|------|------|------|------|-----------|
| `period_subsidence_mm` | 周期沉降量 | mm | `t_data_wash_fcb`（默认） | `total_settle` | 周期末 `total_settle` − 周期初 `total_settle`；**负值表示下沉** |
| `period_rebound_mm` | 周期回弹量 | mm | `t_data_wash_fcb` / `jyb` | `total_settle` | 同上，语义为上升；可与沉降共用计算，展示取符号 |
| `point_subsidence_mm` | 测点累计沉降 | mm | `t_data_wash_fcb` | `total_settle` | 问句指明某时刻/最新一条的 `total_settle`（非差值） |
| `gnss_displacement_2d` | GNSS 水平位移 | mm | `t_data_wash_gnss` | `displacement_2d` | 专题问 GNSS 时用 |
| `gnss_displacement_3d` | GNSS 三维位移 | mm | `t_data_wash_gnss` | `displacement_3d` | 问「位移/GNSS」优先此列；若问沉降且指明 GNSS 则用此列而非 `total_settle` |
| `groundwater_depth` | 地下水埋深 | m | `t_data_wash_dxswj` | `deep` 或 `elevation` | 与沉降禁混用 |
| `pore_pressure` | 孔隙水压力 | kPa | `t_data_wash_kxsylj` | `pressure` | 辅助 |
| `meteo_temp` | 气温 | ℃ | `t_data_wash_qxz` | `temp` | 辅助 |
| `meteo_rain` | 降水 | mm | `t_data_wash_qxz` | `real_time_rain` | 辅助 |
| `fiber_settle` | 光纤沉降 | mm | `t_data_wash_gq` | `total_settle` | 辅助 |

**问句 → 指标消歧（规则）**：

1. 含「GNSS」「三维位移」「水平位移」→ `gnss_displacement_3d` / `2d`  
2. 含「水位」「埋深」「地下水」→ `groundwater_depth`  
3. 含「孔隙」「孔压」→ `pore_pressure`  
4. 含「气温」「降水」「气象」→ 气象类指标  
5. 含「本季度/上季度/本月/近一年」+「沉降/下沉/回弹」→ `period_subsidence_mm`（时间窗由 `time_intent_display` 提供）  
6. 泛化「监测点沉降」「沉降多少」→ `period_subsidence_mm` 或 `point_subsidence_mm`（有无限定时间则取最近/窗口末）  
7. **默认主表**：未指明类型 → `t_data_wash_fcb` + `period_subsidence_mm` 或 `point_subsidence_mm`

### 5.7 `metrics.yaml` 片段（subsidence）

```yaml
version: "2026.08.24"
metrics:
  - id: period_subsidence_mm
    name: 周期沉降量
    synonyms: [沉降量, 下沉量, 累计沉降, 地面沉降, 沉降了多少]
    unit: mm
    grain: station_period
    formula_note: "窗口内 total_settle 终值减初值；主表默认 t_data_wash_fcb"
    forbidden_confusions: [groundwater_depth, gnss_displacement_3d, pore_pressure]
    preferred_tables: [t_data_wash_fcb, t_data_wash_jyb]
    preferred_columns: [total_settle]
    time_column: data_time

  - id: gnss_displacement_3d
    name: GNSS三维位移
    synonyms: [三维位移, 空间位移, GNSS位移]
    unit: mm
    preferred_tables: [t_data_wash_gnss]
    preferred_columns: [displacement_3d, displacement_2d]
    time_column: data_time
    forbidden_confusions: [period_subsidence_mm]

  - id: groundwater_depth
    name: 地下水埋深
    synonyms: [水位, 埋深, 降深, 地下水]
    unit: m
    preferred_tables: [t_data_wash_dxswj]
    preferred_columns: [deep, elevation]
    time_column: data_time
    forbidden_confusions: [period_subsidence_mm]
```

### 5.8 分层标各层数据（预留）

当前库仅有分层标 **最上层清洗数据**（`t_data_wash_fcb`）。工程保留扩展：

```yaml
# layered_fcb_placeholder.yaml
layered_fcb:
  status: placeholder
  future_tables:
    - t_data_wash_fcb_layer_2   # 示例名，待库表落地后改
  note: "问句含「第N层」时暂 warning，或回退 fcb 主表"
```

### 5.9 主/辅策略（链接输入）

| 问句类型 | 主表 | 辅表（季报等） |
|----------|------|----------------|
| 单点沉降/站点查询 | `fcb`（默认）或语义指定表 | 一般不 JOIN |
| 季报/年报「沉降与水位/气象关系」 | `fcb` | `dxswj`、`qxz` 等 **JOIN**（同 `project_name` + 时间对齐） |
| 仅 GNSS/气象/水位 | 对应单表 | — |

---

## 6. 改造范围③：显式 Schema 链接

### 6.1 定义与模块职责

显式 Schema 链接 = 在 LLM 生成 SQL **之前**，产出结构化 **`LinkedSchema`**，并以此 **收窄** 进入 Prompt 的表列目录与校验白名单。

| | 现网 | 本改造 |
|--|------|--------|
| 链接方式 | 隐式（宽 catalog + RAG） | **显式**先链接再生成 |
| 失败形态 | 生成后 Validator 拒绝 | 链接阶段即可 `refuse` 或降级 |
| 可审阅性 | 难解释「为何选这张表」 | `LinkedSchema` 进入 trace |

- 输入：`SemanticBinding` + `QuestionIntent` + 反射元数据 + 表白名单 + 可选 RAG 片段  
- 输出：`LinkedSchema`（tables, columns, joins, suggested_filters, status）  
- Prompt catalog：**默认 `linked_only`**

**代码规划**：`app/nl2sql/schema_linker.py`；`chain.py` 在 SemanticAlign 之后调用。

### 6.2 `LinkedSchema`（建议结构）

```text
LinkedSchema:
  tables: [ {name, reason, score} ]
  columns: [ {table, column, role: measure|time|dim|filter, reason} ]
  joins: [ {left_table.col, right_table.col, reason} ]
  union_tables: [str]              # 跨类型 UNION 时
  suggested_filters: [ {table.col, op, value_from: semantic|intent} ]
  catalog_fingerprint: str
  confidence: float
  status: ok | weak | failed
  fail_reason: str | null
  semantic_version: str
  allowlist_version: str
```

写入 `parsed_intent.linked_schema`；链接拒绝时可设 `gen_fail_reason=link_failed`。

### 6.3 链接输入

1. `SemanticBinding`（指标偏好表列、维度码）  
2. `QuestionIntent`（时间窗、范围/HITL）  
3. `SchemaMetadataService` 反射目录  
4. **预登记白名单**（`table_scope.txt` / allowlist）  
5. 可选：`NL2SQLRAGService` 检索片段（**不得**单独引入白名单外表）

### 6.4 链接策略（已确认 + 最佳实践）

| 策略项 | 决策 |
|--------|------|
| 链接对象 | **仅 8 张事实/维表**，无汇总视图 |
| 白名单 | **8 表全部开放** |
| 单监测类型问句 | 链接 **1 张主事实表**；若需行政区 JOIN `t_station` |
| 泛化「沉降」 | 主表 **`t_data_wash_fcb`**；可选 UNION `jyb`（问句含基岩标时） |
| 跨类型/「所有监测」 | **UNION ALL** 多事实表（结构相近列对齐）或 **多表分别查**（由问句决定）；链接阶段输出 `union_tables` 列表 |
| 多表关联（季报） | **JOIN**：`fcb` + `dxswj`/`qxz`，键 `project_name` + 时间窗；**允许** |
| 聚合 | **允许** `GROUP BY station_id`、窗口起止 `total_settle` 差值等 |
| 失败 | 默认 `on_link_failure=best_effort`（报告）；对话可 `refuse` |

### 6.5 链接算法（一期）

```text
A. 候选表
   metric.preferred_tables ∪ device_type→表映射 ∪ RAG 提示
   ∩ allowlist（8 表）∩ 反射存在
   打分排序，取 Top-K（建议 1～3）

B. 若问句需要行政区/站点过滤
   自动加入 t_station，JOIN：fact.project_name = t_station.name

C. 候选列
   度量列：metric.preferred_columns 或语义角色列
   时间列：metric.time_column 或表级 data_time
   维度列：station_id / station_name / area 等
   ∩ 反射列

D. JOIN
   优先反射 FK；其次 join_whitelist.txt
   禁止臆造关联

D2. 多表
   - 辅助指标：JOIN 辅表 ON project_name + 时间范围重叠
   - 跨类型：UNION 模板或 union_tables 列表

E. 过滤建议
   时间：交给现网 _rewrite_query_filters（链接只标注时间列）
   站点/行政区：suggested_filters，供 Prompt 与程序改写

F. 失败
   无候选表 / 度量列无法映射 / 强制维度全无对齐且策略严格
   → status=failed
```

### 6.6 Catalog 注入策略

| 模式 | 行为 | 配置建议 |
|------|------|----------|
| `linked_only`（推荐） | Prompt catalog **仅** LinkedSchema 内表列 | 正式环境默认 |
| `linked_prefer` | 链接结果置顶，附加少量相关表 | 迁移过渡期 |
| `legacy_wide` | 改造前宽 catalog | 总开关关闭或排障 |

实现：复用 `_format_enriched_schema_catalog`，按 LinkedSchema 过滤 `catalog_tables`；`allowed_tables` / `allowed_columns` 与链接结果对齐，避免「Prompt 窄、校验宽」。

### 6.7 失败策略 `on_link_failure`

| 值 | 行为 |
|----|------|
| `refuse` | 不调用 LLM；响应带 `gen_fail_reason=link_failed` 与 `fail_reason` |
| `best_effort` | 降级 `legacy_wide` 或 `linked_prefer`，打点告警，继续现网生成 |

请求级参数优先于全局默认；**禁止**在基座内等待用户输入。

### 6.8 JOIN 白名单（subsidence 初始）

```text
# join_whitelist.txt — 格式 table.col=table.col
t_data_wash_fcb.project_name=t_station.name
t_data_wash_jyb.project_name=t_station.name
t_data_wash_gnss.project_name=t_station.name
t_data_wash_dxswj.project_name=t_station.name
t_data_wash_kxsylj.project_name=t_station.name
t_data_wash_gq.project_name=t_station.name
t_data_wash_qxz.project_name=t_station.name
```

跨事实表 JOIN（季报）：在 Prompt 中强调 **同 project_name + 时间窗**；反射确认 `station_id` 一致后可补充 `station_id` 等值 JOIN。

---

## 7. Prompt 与 RAG

### 7.1 RAG 摄入（你方后续通过接口）

| 命名空间 | 地降内容建议 |
|----------|--------------|
| `nl2sql_schema` | 8 表字段说明（来自 `226大模型数据库.docx` + 反射校验） |
| `nl2sql_biz_knowledge` | 监测类型说明、主/辅关系、`project_name`↔`t_station`、周期沉降口径 |
| `nl2sql_qa_examples` | 问法→标准 SQL；按五类 `analysis_type` 标签 |

**namespace 不拆分**；锅炉/地降靠 **部署 domain** 只摄入对应业务文档（避免同进程混库）。

### 7.2 NL2SQL 主 Prompt（`nl2sql` scene）

地降版须声明：

- 方言：**PostgreSQL**  
- 仅使用 LinkedSchema 内表列  
- 沉降主指标：`total_settle`；GNSS 用 `displacement_*`  
- 行政区：**JOIN `t_station`**，`area` 过滤  
- 允许聚合、子查询、UNION（只读）  
- 禁止 TiDB 专属写法  

版本建议：`NL2SQL_PROMPT_DEFAULT_VERSION=v2_subsidence`（在 `prompts.yaml` 或 `prompts_subsidence.yaml` 中定义）。

### 7.3 范围 LLM 提示词

默认 **rule**，可不依赖 `nl2sql_scope_parse`。若开启 LLM，应为地降单独模板（`nl2sql_scope_parse_subsidence`），与锅炉分离。

---

## 8. 实施分期与交付物

### 8.1 阶段规划（配置 + 语义 + 链接）

| 阶段 | 内容 | 交付物 | 状态 |
|------|------|--------|------|
| **P0 / M0** | `NL2SQL_BUSINESS_DOMAIN`、profile（含 **db.* 连接默认**）、8 表白名单、PG 方言/异步驱动 | `configs/nl2sql_business/subsidence/*` | **已完成** |
| **P1 / M1** | `semantic_layer.py`、规则对齐、`parsed_intent.semantic` | 语义单测 | **已完成** |
| **P2 / M2** | `schema_linker.py`、catalog 收窄、`refuse`/`best_effort`、cache 指纹含 domain | LinkedSchema + 单测 | **已完成** |
| **P3** | 地降范围词表 / rule / `@station_*` `@district` 改写 | `scope_lexicon.json`（含 83 站） | **已完成** |
| **P4** | 五类 `analysis_plan/synthesis_*`、`v2_subsidence`、RAG 源文件与 QA 种子 | `prompts.yaml` + `rag/*` | **已完成** |
| **P5** | `boiler_four_tube` 配置包（db.*、表白名单、语义默认关） | `boiler_four_tube/*` | **已完成**（锅炉语义 YAML 可后补） |

**仍依赖服务器/联调（非代码缺口）**：地降 PG 反射与执行、RAG 三命名空间摄入、黄金集可执行率评测、五类报告端到端。

**M0 准入**：有结构文档（或可反射）；有白名单初稿；有指标口径责任人。

**地降工程化前置（与 P0 并行，准出条件）**：

| 项 | 说明 | 状态 |
|----|------|------|
| 业务库连接 | `DB_*` 指向地降只读库；**勿将明文密码写入 git** | 部署侧 |
| 方言 | 地降为 **PostgreSQL**；Prompt/改写须 PG 分支（§3.2） | **代码已完成** |
| 表白名单 | 8 表核心集，勿全库开放 | **已完成** |
| RAG 摄入 | `nl2sql_schema` / `biz` / `qa` 换地降内容 | **源文件已备**；摄入走 `/rag/documents/upsert` |
| 时间字段 | 统一 `data_time` | **语义资产已标注** |
| 结构真源 | 以 `数据库说明.md` + 反射为准 | 联调验证 |
| 站点词表 | `t_station` → `scope_lexicon.json` · `stations` | **已写入 83 站**（手维，见企业级简版 §4.4） |

### 8.2 环境变量速查（subsidence 部署）

| 变量 | 建议值 |
|------|--------|
| `NL2SQL_BUSINESS_DOMAIN` | `subsidence` |
| `NL2SQL_SEMANTIC_LINK_ENABLED` | `true`（资产齐后） |
| `NL2SQL_SQL_DIALECT` | `postgres` |
| `NL2SQL_INTENT_PARSE_MODE` | `rule` |
| `NL2SQL_SCOPE_SQL_REWRITE_ENABLED` | `true` |
| `NL2SQL_PROMPT_DEFAULT_VERSION` | `v2_subsidence` |
| `NL2SQL_SCHEMA_LINK_CATALOG_MODE` | `linked_only` |
| `NL2SQL_INJECT_PARSED_INTENT` | `true` |
| `DB_*` | 见 §1.1 |

（`ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT` 可由 profile 注入，不必重复手写。）

### 8.3 验收标准（功能）

- [x] 8 表白名单与 JOIN 配置落地（反射/执行需服务器验证）  
- [x] 语义+链接：泛化沉降 → `fcb`；GNSS → `gnss`（单测 + 黄金集链接测）  
- [x] 行政区/站点 rule 解析与 `@district`/`@station_*` 改写  
- [x] 周期沉降口径写入语义资产与 Prompt（`total_settle` 差值）  
- [x] `confirmed_scope` / `time_intent_text` / `structured_filters` 契约保留  
- [x] 五类 `analysis_type` 的 plan/synthesis/report 模板  
- [x] `boiler_four_tube` profile：`semantic_link_enabled=false` + 表白名单文件  
- [ ] 地降库联调：朝阳区+沉降 JOIN/`fcb`/时间窗可执行  
- [ ] RAG 三命名空间已摄入；QA 槽位可回放  
- [ ] 黄金集：相对 baseline 的主表/度量列正确率报告（≥30 条，可执行联调）  
- [ ] `SEMANTIC_LINK_ENABLED=false` 现网锅炉部署回归无劣化  

### 8.4 立即行动项（联调侧）

1. 服务器配置 `NL2SQL_BUSINESS_DOMAIN=subsidence` + `DB_*`（PG）并做反射烟测。  
2. `POST /rag/documents/upsert` 摄入 `configs/nl2sql_business/subsidence/rag/*.md`。  
3. 用 `eval/golden_set.json` 扩到 ≥30 条并跑可执行率对比。  
4. 锅炉部署保持 `boiler_four_tube`，确认回归集。  

### 8.5 代码交付索引（本轮已落）

| 路径 | 说明 |
|------|------|
| `app/nl2sql/nl2sql_business_profile.py` | domain 配置包加载 |
| `app/nl2sql/semantic_layer.py` / `schema_linker.py` / `sql_dialect.py` | 语义+链接+PG |
| `app/nl2sql/scope_parser_subsidence.py` / `scope_sql_rewrite.py` | 地降范围 |
| `configs/nl2sql_business/subsidence/**` | 配置包、词表、语义、RAG 源、黄金集 |
| `configs/nl2sql_business/boiler_four_tube/**` | 锅炉配置包（语义默认关） |
| `configs/prompts.yaml` | `v2_subsidence` + `analysis_*_subsidence_*` |
| `tests/test_nl2sql_*.py` | profile/链接/改写/黄金集主表 |

---

## 9. 请求/响应契约（建议）

### 9.1 请求增量（可选字段）

```text
on_link_failure: "refuse" | "best_effort" | null
structured_filters: { ... }   # 已确认约束；字段随 domain 资产（站点/行政区 或 锅炉/受热面等）
```

现有字段继续有效：`time_intent_text`、`confirmed_scope`、`scope_intent_text`、`original_query`、`analysis_type`、`plan_item_id` 等。

### 9.2 响应增量（建议）

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

## 10. 代码改造清单（汇总）

| 模块 | 动作 | 说明 |
|------|------|------|
| `app/core/config.py` | 扩展 | `NL2SQLBusinessProfile`、profile 加载 |
| `app/nl2sql/semantic_layer.py` | **新增** | 资产加载、对齐、版本校验 |
| `app/nl2sql/schema_linker.py` | **新增** | `_link_schema` / `LinkedSchema` |
| `app/nl2sql/chain.py` | 扩展 | 插管；PG 方言；catalog 收窄；后半段原则上不动 |
| `app/nl2sql/time_intent_display.py` | 扩展 | PG 时间表达式（或方言适配器） |
| `app/nl2sql/scope_parser_rule.py` | 扩展 | subsidence 维度（或插件） |
| `app/nl2sql/scope_sql_rewrite.py` | 扩展 | `@station_id`、`@district`；占位符随资产扩展 |
| `app/nl2sql/scope_lexicon.py` | 扩展 | 默认路径跟 profile |
| `app/nl2sql/prompt_builder.py` / intent display | 扩展 | 注入语义口径 + 链接摘要 |
| `app/nl2sql/sql_cache.py` | 适配 | cache key 纳入 semantic_version、allowlist 指纹 |
| `app/models/nl2sql.py` | 扩展 | `on_link_failure`；响应语义/链接元数据 |
| `NL2SQLService` | 扩展 | 链接 `refuse` 短路；不实现多轮等待 |
| `question_scope_models.py` / intent 展示 | 扩展 | semantic / linked_schema 摘要 |
| `configs/nl2sql_business/**` | **新增** | 按 domain 配置包 |
| `configs/prompts*.yaml` | 扩展 | 分业务 nl2sql + analysis_plan |
| `app/app-deploy/.env.example` | 文档化 | |

**明确不改（一期）**：Executor 执行协议内核、L1 时间骨架算法主体、客服/分析编排图。

---

## 11. 测试与验收清单

### 11.1 单测

- [x] 同义词 / 指标命中与禁混用（`test_nl2sql_semantic_business_profile`）  
- [x] 链接结果 ∩ allowlist；`linked_only` 白名单外表不进 catalog  
- [x] 锅炉 domain：`semantic_link_enabled=false` + 表白名单  
- [x] 地降黄金集主表链接（`eval/golden_set.json`，当前 15 条，可扩）  
- [ ] `refuse` 不调用 LLM（需 chain 级 mock，联调前可补）  
- [ ] `best_effort` 降级路径可生成（同上）  

### 11.2 集成 / 黄金集

- [ ] 地降只读库联调：反射表与 `preferred_tables` 一致  
- [ ] 黄金集对比报告（表列正确率、可执行率、口径追溯；建议扩至 ≥30）  
- [ ] 综合分析多槽批跑：无人工输入、无澄清挂起  
- [ ] 客服 `data_query` 烟测（若同部署）  

### 11.3 对外可演示口径

- [x] 问句 → 语义命中 → LinkedSchema（单测可演示路径）  
- [ ] 问句 → SQL → rows（需 PG + RAG 联调）  
- [x] 不确定时机读失败策略已实现（`refuse` / `best_effort`）；基座不阻塞等待  

---

## 12. 风险与对策

| 风险 | 对策 |
|------|------|
| PG 与 TiDB 改写混用 | `NL2SQL_SQL_DIALECT` 强制分支；单测覆盖 |
| `project_name` 与 `t_station.name` 不一致 | 摄入前数据质量检查；链接 trace 暴露 JOIN 失败率 |
| 分层各层表未上线 | placeholder + warning |
| 同进程误用锅炉/地降配置 | **一套部署一个 domain**；缓存键含 domain 指纹 |
| RAG 未摄入 | 链接仍靠语义资产 + 反射；biz/qa 渐进补齐 |
| UNION 多表列不一致 | 链接层输出列映射模板；Prompt 示例 |
| 指标口径无法业务确认 | M1 只上同义词+监测类；未确认指标不进开放集 |
| 链接过严 → 零 SQL | 过渡 `best_effort` + `linked_prefer`；放宽 Top-K |
| 链接链错 | 黄金集门禁；trace 审阅；坏例回流 QA |
| 语义资产把密钥写进仓库 | 资产仅结构与口径；连接只走环境变量 |
| 锅炉与地降字段互相污染 | 部署级隔离配置包；禁止混用 domain |
| 误把查询类型/澄清做大 | 范围冻结为 G1–G6 |

---

## 13. 附录 A：`t_station` 字段（来自 226 文档）

| 字段 | 说明 |
|------|------|
| `id` | 主键 |
| `name` | 监测场地名称（与 `project_name` 关联） |
| `code` | 站点编码 |
| `lon` / `lat` | 经纬度 |
| `area` | **行政区** |

## 14. 附录 B：事实表公共字段模式

各 `t_data_wash_*` 均含：`id`, `data_id`, `station_id`, `station_name`, `data_time`, `insert_time`, `project_name`, `expand`（及表特有度量列）。

## 15. 附录 C：监测类型 → 表映射（地降，以物理库为准）

| 监测类（device_type） | 表名 | 时间字段 |
|----------------------|------|----------|
| 地下水 | `t_data_wash_dxswj` | `data_time` |
| 分层标 | `t_data_wash_fcb` | `data_time` |
| GNSS | `t_data_wash_gnss` | `data_time` |
| 光纤 | `t_data_wash_gq` | `data_time` |
| 基岩标 | `t_data_wash_jyb` | `data_time` |
| 孔隙水 | `t_data_wash_kxsylj` | `data_time` |
| 气象站 | `t_data_wash_qxz` | `data_time` |
| 站点维表 | `t_station` | — |

指标 → 表/列的 `preferred_*` **须**与 §5.6 开放指标及业务确认一致后再写入 `metrics.yaml`。

## 16. 附录 D：文档与代码索引

| 文档/代码 | 用途 |
|-----------|------|
| **本文** | NL2SQL 基座 **唯一**改造总方案 |
| `enterprise-level_transformation_docs/企业级NL2SQL实现方案.md` | 现网基座全链路 |
| `NL2SQL基座五阶段改造方案.md` | 五阶段背景 |
| `docs/NL2SQL自然语言时间和范围窗口解析&改写改造落地方案.md` | 时间/范围改写 |
| `数据库说明.md` / `226大模型数据库.docx` | 地降库结构真源 |
| `app/nl2sql/chain.py` | 插管主挂载点 |
| `app/nl2sql/question_intent.py` | 现网意图入口 |
| `app/nl2sql/schema_service.py` | 反射 |
| `app/nl2sql/rag_service.py` | 三命名空间 RAG |
| `configs/prompts.yaml` · `nl2sql` | SQL 生成提示词 |

---

*本文是 NL2SQL 基座改造的唯一维护文档。若扩展查询类型意图或结果图表契约，另开文档，避免稀释语义+链接的准确率目标。密码、生产 IP 以运维侧为准，勿写入 git。*
