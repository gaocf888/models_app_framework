# NL2SQL 当前完整实现逻辑说明（代码对照版）

> 本文描述**当前仓库真实代码行为**（而非理想化设计），用于评审、排障与运维交接。  
> **版本对齐**：2026-08-25（多业务域配置包 + 语义对齐 + Schema 链接 + PG 方言）。  
> 关键入口：`app/api/nl2sql.py`、`app/services/nl2sql_service.py`、`app/nl2sql/chain.py`、`validator.py`、`executor.py`；配置包 `nl2sql_business_profile.py` / `intent_config.py`；语义/链接 `semantic_layer.py` / `schema_linker.py`；方言 `sql_dialect.py`；意图含 `scope_parser_subsidence.py`。  
> 设计与验收：`docs/基于地降所项目改造/NL2SQL基座改造.md`；企业流程图：`企业级NL2SQL基座实现方案.md`；缓存：`docs/NL2SQL缓存实现方案.md`；技术映射：`framework-guide/NL2SQL整体实现技术说明.md`。

---

## 1. 入口与调用形态

当前 NL2SQL 有 **四种** 产品入口，底层均经 **`NL2SQLService.query`**：

1. HTTP 直连：`POST /nl2sql/query`。  
2. 智能客服：`data_query` → `nl2sql_answer`（`record_conversation=False`）；可读 **`gen_fail_reason`**。  
3. 综合分析 V2：`POST /analysis/run-with-nl2sql*` → `_execute_data_plan` 多次 `query`；含地降五类 **`subsidence_*`**。  
4. 看图诊断：`POST /analysis/run-img-diag*` NL 臂；可传 **`confirmed_scope`**。

**部署域**：`NL2SQL_BUSINESS_DOMAIN=boiler_four_tube | subsidence`（一套进程一个 domain），加载 `configs/nl2sql_business/<domain>/`。

请求/响应：
- `NL2SQLQueryRequest`：`question`；可选 `time_intent_text`、`confirmed_scope`、`analysis_type`、`plan_item_id` 等。  
- `NL2SQLQueryResponse`：`sql`、`rows`；可选 **`parsed_intent`**（含 `semantic` / `linked_schema`）、**`gen_fail_reason`**（如 `link_failed:…`）。

---

## 2. 端到端主流程（服务层视角）

`NL2SQLService.query` 顺序：

1. 校验 `user_id`，可选写会话。  
2. `NL2SQL_QUERY_COUNT.inc()`。  
3. `NL2SQLChain.generate_sql_with_validation_context(...)`：  
   **意图 →（可选）语义对齐 →（可选）Schema 链接 → Schema 反射 →（可选）plan → RAG → L2/L1 → Prompt/LLM → 时间·范围改写（含方言）→ 校验**。  
   链接 **refuse** 时 SQL 可为空并带失败上下文；服务层写入 **`gen_fail_reason`**。  
4. 执行闭环：`EXPLAIN?` → `execute`；失败可 `refine_sql_after_executor_error`（有界）。  
5. 返回 `NL2SQLQueryResponse(sql, rows[, parsed_intent, gen_fail_reason])`。

---

## 3. SQL 生成链路（NL2SQLChain）

### 3.1 模型初始化与后端选择

- 优先 LangChain `ChatOpenAI`；否则 `VLLMHttpClient`。  
- 采样：`NL2SQL_CHAT_TEMPERATURE`（默认 `0`）、`TOP_P`、`SEED`。

### 3.1 bis 业务配置包与意图配置

- `get_nl2sql_business_profile()` 按 domain 读 `profile.yaml`。  
- `intent_config`：表白名单指纹、`semantic_link_enabled`、`schema_link_catalog_mode`、`on_link_failure`、方言、Prompt 版本、词表路径等。  
- 优先级：**显式 env ＞ profile ＞ 代码默认**。

### 3.2 Schema 刷新与可用性判定

- `_ensure_schema_refreshed_once()` → `refresh_from_db()`（MySQL/TiDB 或 PostgreSQL+asyncpg）。  
- `schema_from_db`：表集非空且不只是 demo `orders`。

### 3.2 bis 语义对齐与 Schema 链接（可选）

当 `semantic_link_enabled()`：

1. **`align_semantics`**（`semantic_layer.py`）→ `parsed_intent.semantic`（含 `semantic_version`）。  
2. **`link_schema`**（`schema_linker.py`）→ `parsed_intent.linked_schema`（主表、度量列、建议过滤等）。  
3. **catalog**：`linked_only`（默认地降）仅链接表列；`linked_prefer` / `legacy_wide` 放宽。  
4. **失败**：`on_link_failure=refuse` → 不调 LLM，上层 `gen_fail_reason=link_failed:…`；`best_effort` → 降级宽 catalog 继续生成。

锅炉域默认 **关闭** 语义链接（`legacy_wide`）。

### 3.3 可选规划（planner）

- 仅 LangChain 可用时可能 `_plan`；`NL2SQL_DISABLE_PLANNER_WHEN_DB_SCHEMA=true` 且真实库可用时默认跳过。

### 3.4 RAG 检索

三 namespace联合检索：`nl2sql_schema` / `nl2sql_biz_knowledge` / `nl2sql_qa_examples`。  
QA 指纹过滤、prefetch 等行为同前（`NL2SQL_QA_*`）。地降 RAG 源文件：`configs/nl2sql_business/subsidence/rag/`。

### 3.4 bis 可选：校验通过后写入 QA 向量（`NL2SQL_QA_FEEDBACK_ENABLED`）

- 默认关；新鲜 LLM 路径校验通过后 `upsert_nl2sql_auto_qa_pair`；五元组去重含 `plan_template_version`。运维 `GET`/`PATCH /rag/nl2sql-auto-qa`。

### 3.5 白名单、表列映射与规则加载

- 优先反射 + **配置包/链接结果** 收窄；失败回退片段抽取。  
- `NL2SQLValidationContext` 供执行期 refine 复用。  
- 实体规则：`NL2SQL_ENTITY_RULES_FILE` / `_RULES` 或 profile `entity_rules_file`。

### 3.6 Prompt 构建

- scene=`nl2sql`；版本 `NL2SQL_PROMPT_DEFAULT_VERSION` 或 profile（锅炉 `v2`，地降 `v2_subsidence`）。  
- `{{NL2SQL_SCHEMA_CATALOG}}`：linked catalog 或宽表白名单 / RAG hints。  
- `NL2SQL_INJECT_PARSED_INTENT` 时可注入时间/范围 + **语义指标摘要 / 链接主表**。

### 3.7 生成、归一化与校验

- `normalize_sql` → `_validate_sql`（只读、白名单、列–表绑定、实体规则）→ 可 `_refine_sql`。

### 3.8 问句意图解析（时间 + 实体范围）

入口 **`resolve_question_intent`**，每请求一次。

| 维度 | 实现 | 配置 |
|------|------|------|
| **时间窗** | 始终规则 `time_intent_display` | 无独立开关 |
| **范围·锅炉** | `scope_parser_rule` / 可选 LLM | `NL2SQL_INTENT_PARSE_MODE` |
| **范围·地降** | `scope_parser_subsidence` + 配置包词表 | domain=`subsidence` |
| **HITL** | `confirmed_scope` → `human_confirmed`（仅范围） | 看图诊断等 |
| **SQL 改写** | `@t_*` + 方言适配；锅炉 `@unit/@device…`；地降 `@district/@station_*` | `NL2SQL_SCOPE_SQL_REWRITE_ENABLED`（默认 true） |
| **可观测** | Prompt / API `parsed_intent` / trace | `NL2SQL_INJECT_*` / `RESPONSE_INCLUDE_*` / `TRACE_INCLUDE_*` |

**防污染**：综合分析传 `time_intent_text=用户原句`。

### 3.9 SQL 方言适配

- `sql_dialect.adapt_time_window`：把规则解析产生的 MySQL 风格时间表达式适配为 PostgreSQL（地降执行前改写阶段调用）。  
- Prompt `v2_subsidence` 侧也避免写死仅 MySQL 函数。

---

## 4. refine 双闭环

### 4.1 生成期 refine（`_refine_sql`）

校验失败 → 带 error 再生成 → 再 normalize + 全量校验（含改写）。

### 4.2 执行期 refine（`refine_sql_after_executor_error`）

`EXPLAIN`/`execute` 失败且允许 refine → 新 SQL 仍用原 `vctx` 边界再验；上限 `NL2SQL_MAX_EXEC_REFINES`。

---

## 5. 校验细节：列表绑定为何能拦截“列挂错表”

`validate_column_table_binding`：主查询 FROM 别名 → `alias.column` 对照反射 `table_columns_map`；不匹配则 `column-table binding failed`。

---

## 6. 执行器行为

- `explain` / `execute`；PG 经 asyncpg，建连不加 MySQL `charset`。  
- 日志可打完整 SQL preview。

---

## 7. 关键运行开关（默认值）

| 变量 | 默认 / 说明 | 作用 |
|------|-------------|------|
| `NL2SQL_BUSINESS_DOMAIN` | 部署必选其一 | 加载配置包 |
| `NL2SQL_SEMANTIC_LINK_ENABLED` | 地降 profile 常 true | 语义+链接 |
| `NL2SQL_SCHEMA_LINK_CATALOG_MODE` | 地降 `linked_only` | catalog 收窄 |
| `NL2SQL_ON_LINK_FAILURE` | 常 `best_effort` | refuse / best_effort |
| `NL2SQL_EXPLAIN_BEFORE_EXECUTE` | `false` | 执行前 EXPLAIN |
| `NL2SQL_REFINE_ON_EXEC_ERROR` | `true` | 执行期 refine |
| `NL2SQL_MAX_EXEC_REFINES` | `1` | refine 轮数 |
| `NL2SQL_DISABLE_PLANNER_WHEN_DB_SCHEMA` | `true` | 跳过 planner |
| `NL2SQL_ENTITY_RULES_FILE` / `_RULES` | 空 | 否定实体规则 |
| `NL2SQL_INTENT_PARSE_MODE` | `rule` | 范围解析模式 |
| `NL2SQL_SCOPE_SQL_REWRITE_ENABLED` | `true` | 范围占位符改写 |
| `NL2SQL_SCOPE_LEXICON_FILE` | 可被 profile 覆盖 | 范围词典 |
| `NL2SQL_INJECT_PARSED_INTENT` | `false` | Prompt 注入意图 |
| `NL2SQL_RESPONSE_INCLUDE_PARSED_INTENT` | `false` | API 返回意图 |
| `NL2SQL_TRACE_INCLUDE_QUESTION_INTENT` | `true` | 分析 trace |

---

## 8. Chatbot 分支补充

`data_query` 复用同一内核；`chatbot_nl2sql_answer` 可消费 **`gen_fail_reason`**（如链接失败提示），再 `summarize_nl2sql_with_llm`。

---

## 8b. 综合分析 V2（`run-with-nl2sql`）补充

- `_execute_data_plan` 按 dependency 分层、默认同层并行 `query`。  
- 锅炉专项 + 地降 **`subsidence_daily|weekly|monthly|quarterly|yearly`**（Prompt：`analysis_plan_subsidence_*` / `analysis_synthesis_subsidence_*` 等）。  
- 详：`企业级综合分析实现和使用说明.md`。

---

## 9. 已知实现边界（当前版本）

1. 列–表绑定主要针对主查询 FROM；极复杂嵌套可能部分跳过。  
2. 实体规则仅否定拦截。  
3. 执行期 refine 依赖 LangChain。  
4. 一套部署一个 domain，不在单次请求切换。  
5. 一期不做基座内多轮澄清 / 图表引擎（见 `NL2SQL基座改造.md`）。

---

## 10. 建议配套阅读

- `enterprise-level_transformation_docs/企业级NL2SQL基座实现方案.md`
- `docs/基于地降所项目改造/NL2SQL基座改造.md`
- `framework-guide/NL2SQL整体实现技术说明.md`
- `docs/NL2SQL系统概要设计.md`
- `docs/NL2SQL自然语言时间和范围窗口解析&改写改造落地方案.md`
- `docs/NL2SQL缓存实现方案.md`
