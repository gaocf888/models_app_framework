# NL2SQL 当前完整实现逻辑说明（代码对照版）

> 本文描述**当前仓库真实代码行为**（而非理想化设计），用于评审、排障与运维交接。  
> 关键入口：`app/api/nl2sql.py`、`app/services/nl2sql_service.py`、`app/nl2sql/chain.py`、`app/nl2sql/validator.py`、`app/nl2sql/executor.py`。

---

## 1. 入口与调用形态

当前 NL2SQL 有两条入口，底层复用同一套服务：

1. HTTP 直连：`POST /nl2sql/query`  
2. 智能客服：意图为 `data_query` 时调用 `NL2SQLService.query(..., record_conversation=False)`，再由 `chatbot_nl2sql_answer.py` 将 `sql + rows` 转自然语言。

请求/响应模型：
- `NL2SQLQueryRequest(user_id, session_id, question)`
- `NL2SQLQueryResponse(sql, rows)`

---

## 2. 端到端主流程（服务层视角）

`NL2SQLService.query` 的顺序如下：

1. 校验 `user_id`，可选写入会话（用户问题）。
2. 记指标：`NL2SQL_QUERY_COUNT.inc()`。
3. 调用 `NL2SQLChain.generate_sql_with_validation_context(question, user_id)`，得到：
   - `sql`：生成 SQL（可能为空）
   - `vctx`：`NL2SQLValidationContext`（允许表列、`schema_ok`、`table_columns`）
4. 根据开关进入执行闭环：
   - `NL2SQL_EXPLAIN_BEFORE_EXECUTE`（默认 `false`）
   - `NL2SQL_REFINE_ON_EXEC_ERROR`（默认 `true`）
   - `NL2SQL_MAX_EXEC_REFINES`（默认 `1`）
5. 当 `sql` 非空时循环：
   - 可选先 `executor.explain(sql)`
   - 再 `executor.execute(sql)`
   - 任一失败：若允许 refine 且剩余次数 > 0，则 `chain.refine_sql_after_executor_error(...)` 产出新 SQL 并重试；否则退出。
6. 可选写会话（错误摘要 + 最终 SQL），返回 `NL2SQLQueryResponse(sql, rows)`。

---

## 3. SQL 生成链路（NL2SQLChain）

### 3.1 模型初始化与后端选择

- 优先尝试 LangChain `ChatOpenAI`；
- 若不可用，回退 `VLLMHttpClient`；
- NL2SQL 采样参数独立配置：
  - `NL2SQL_CHAT_TEMPERATURE`（默认 `0`）
  - `NL2SQL_CHAT_TOP_P`（默认 `0.95`）
  - `NL2SQL_CHAT_SEED`（可选）

### 3.2 Schema 刷新与可用性判定

- 请求初次进入 chain 时，`_ensure_schema_refreshed_once()` 调 `SchemaMetadataService.refresh_from_db()`。
- `schema_from_db` 判定逻辑：表集合不为空且不只是 demo `orders`。

### 3.3 可选规划（planner）

- 仅 LangChain 可用时才可能执行 `_plan`；
- 当 `NL2SQL_DISABLE_PLANNER_WHEN_DB_SCHEMA=true` 且真实库可用时，默认跳过 planner（避免虚构表名污染检索 query）。

### 3.4 RAG 检索

`NL2SQLRAGService` 在三个 namespace 联合检索并去重：
- `nl2sql_schema`
- `nl2sql_biz_knowledge`
- `nl2sql_qa_examples`

检索条数受以下开关影响：
- `NL2SQL_SCHEMA_NAMESPACE_TOP_K`
- `NL2SQL_RAG_MAX_SCHEMA_CHUNKS`
- `NL2SQL_RAG_MAX_BIZ_CHUNKS`
- `NL2SQL_RAG_MAX_QA_CHUNKS`

### 3.5 白名单、表列映射与规则加载

- 优先从 DB 反射构建 `allowed_tables`/`allowed_columns`；失败时回退到片段抽取。
- 当 `schema_ok=True` 时构建 `table_columns_map`（表 -> 列集合）。
- 组装 `NL2SQLValidationContext`，供执行期 refine 复用。
- 可选加载实体规则（否定规则）：
  - `NL2SQL_ENTITY_RULES_FILE`
  - `NL2SQL_ENTITY_RULES`

### 3.6 Prompt 构建

- 从 `PromptTemplateRegistry(scene="nl2sql")` 取模板（默认 `NL2SQL_PROMPT_DEFAULT_VERSION=v2`）。
- 若模板含 `{{NL2SQL_SCHEMA_CATALOG}}`，按优先级注入：
  1) DB 全库 catalog（含 FK）  
  2) RAG hints catalog  
  3) 降级提示文案
- 最终由 `PromptBuilder.build(...)` 拼装问题、schema 片段、catalog 与输出约束。

### 3.7 生成、归一化与校验

- LLM 返回 SQL 后先 `normalize_sql`：
  - 去 markdown fence
  - 去 `sql\n` 前缀
  - 引号外空白压成单行
- 再走 `_validate_sql` 多层校验：
  1) 只读安全（仅 `SELECT/WITH`，拦截写操作关键词）  
  2) 标识符白名单（表、限定列）  
  3) 列–表绑定（`alias.column` 是否属于 alias 对应物理表）  
  4) 实体规则（问题关键词 + SQL 正则命中即拦截）

若失败且 LangChain 可用，则进入生成期 refine。

---

## 4. refine 双闭环

### 4.1 生成期 refine（`_refine_sql`）

触发条件：初稿 SQL 校验失败。  
输入：`question + original_sql + validation_error`。  
输出：一条新的仅 SELECT SQL；随后再次 `normalize + 全量校验`。

### 4.2 执行期 refine（`refine_sql_after_executor_error`）

触发条件：`EXPLAIN` 或 `execute` 报错，且允许 refine。  
输入：`question + bad_sql + MySQL/executor error`。  
关键点：新 SQL 仍需用 `vctx` 复用原校验边界再验，避免“修正绕过校验”。  
次数上限：`NL2SQL_MAX_EXEC_REFINES`。

---

## 5. 校验细节：列表绑定为何能拦截“列挂错表”

`SQLValidator.validate_column_table_binding` 关键机制：

1. 从主查询提取 `FROM` 子句（忽略字符串字面量和括号嵌套影响）；
2. 解析别名映射 `alias -> table`；
3. 扫描 SQL 里 `a.col` 或 `table.col`；
4. 对照反射得到的 `table_columns_map` 校验列归属；
5. 不匹配则报错：`column-table binding failed: ...`。

该能力主要解决“列名确实存在，但属于另一个表”导致的假通过。

---

## 6. 执行器行为

`SQLExecutor` 提供两类动作：

- `explain(sql)`：执行 `EXPLAIN <sql>`（可选预检）
- `execute(sql)`：执行真实查询并返回 `List[dict]`

当前日志行为：`preview` 打印完整 SQL（不截断）。

---

## 7. 关键运行开关（默认值）

| 变量 | 默认 | 作用 |
|------|------|------|
| `NL2SQL_EXPLAIN_BEFORE_EXECUTE` | `false` | 执行前是否先 EXPLAIN |
| `NL2SQL_REFINE_ON_EXEC_ERROR` | `true` | EXPLAIN/执行失败是否尝试执行期 refine |
| `NL2SQL_MAX_EXEC_REFINES` | `1` | 执行期 refine 最大轮数 |
| `NL2SQL_DISABLE_PLANNER_WHEN_DB_SCHEMA` | `true` | 真实库就绪时默认跳过 planner |
| `NL2SQL_ENTITY_RULES_FILE` | 空 | 实体规则文件（若设且存在则读文件） |
| `NL2SQL_ENTITY_RULES` | 空 | 内联实体规则 JSON（仅在未使用文件时读取） |

---

## 8. Chatbot 分支补充

`data_query` 分支复用相同 NL2SQL 内核，只在最后新增一步：
- `summarize_nl2sql_with_llm(user_query, sql, rows)` 生成自然语言答复。

因此“SQL 的正确性、稳定性、闭环行为”与 HTTP 直连保持一致。

---

## 9. 已知实现边界（当前版本）

1. 列–表绑定主要针对主查询 `FROM` 别名，极复杂嵌套子查询场景可能部分跳过（实现复杂度权衡）。  
2. 实体规则目前仅支持“否定拦截”模式，不是正向白名单（例如“必须 JOIN 某表”）。  
3. 执行期 refine 依赖 LangChain；无 LangChain 时只保留一次生成与一次执行路径。  

---

## 10. 建议配套阅读

- `enterprise-level_transformation_docs/企业级NL2SQL实现方案.md`
- `framework-guide/NL2SQL整体实现技术说明.md`
- `docs/NL2SQL系统概要设计.md`
