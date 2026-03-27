# NL2SQL 整体实现技术说明

> 本文描述**当前仓库已实现**的 NL2SQL（自然语言转 SQL）技术方案：基于 **LLM + RAG + Schema 元数据 + 安全执行** 的企业级骨架实现。  
> 配套文档：`docs/NL2SQL系统概要设计.md`（总体设计）、`docs/大小模型应用技术架构与实现方案.md`（4.6 节）、`memory-bank/02-components.md`（组件关系）。

---

## 文档结构（阅读导航）

| 章节 | 内容 |
|------|------|
| **§1 总体技术概览** | 方案总体叙述、能力表、架构图与时序图 |
| **§2 模块与文件映射** | 代码入口速查表 |
| **§3 详细说明** | 按「Schema 元数据 → RAG → Prompt → LLM → 校验与修正 → 执行 → 会话与指标」展开 |
| **§4 配置与环境变量** | 与 `AppConfig.llm`、`DatabaseConfig` 对齐 |
| **§5 HTTP API** | `/nl2sql/query` 行为说明 |
| **§6 典型调用链** | 从 HTTP 到 DB 的端到端链路 |
| **§7 与 RAG/GraphRAG 的关系** | 如何依赖通用 RAG 基座与命名空间设计 |
| **§8 后续演进建议** | 与 `docs/NL2SQL系统概要设计.md` TODO 对齐 |

---

## 1. 总体技术概览

### 1.1 从使用视角看整体流程

在当前基座中，NL2SQL 的整体使用流程可以概括为两大步骤：**知识摄入 → 自然语言查询**。

1. **知识摄入：将 Schema / 业务知识 / 问答样例写入 RAG 知识库**  
   - **Schema 元数据加载**：  
     1. `SchemaMetadataService` 在启动时加载一套内置 Demo Schema（如 `orders` 表），便于无 DB 环境快速试跑；  
     2. 在接入真实数据库后，可调用 `SchemaMetadataService.refresh_from_db()`，基于 `DatabaseConfig.url` 通过 SQLAlchemy 反射真实表结构，刷新内存中的 `TableSchema` 映射。  
   - **通过通用 RAG 实现 NL2SQL 知识摄入**：  
     1. 将从 Schema/业务文档/内部知识库中整理出的文本片段（“表/字段说明”“业务规则”“NL2SQL 示例问答”等）按照类型组织为三个集合：schema 片段、biz 片段、qa 片段；  
     2. 调用 `NL2SQLRAGService.index_schema_snippets(...)` / `index_biz_knowledge(...)` / `index_qa_examples(...)`，内部会委托给 `RAGService.index_texts(..., namespace=nl2sql_schema/biz_knowledge/qa_examples)` 将片段写入通用向量库；  
     3. 这样，NL2SQL 相关知识与其他 RAG 场景共享同一个向量库实例，但通过 `namespace` 实现逻辑隔离。  
   - **关键配置与依赖**：  
     - 数据库连接：`DB_URL`（或 `DB_USER` / `DB_PASSWORD` / `DB_HOST` / `DB_NAME` 组合）；  
     - 通用 RAG 配置：`RAG_VECTOR_STORE_TYPE`、`RAG_FAISS_INDEX_DIR`、嵌入模型相关环境变量（详见 RAG 文档）。  

2. **自然语言查询：通过 NL2SQL 实现数据查询**  
   - **调用入口**：  
     - 上游系统通过 `POST /nl2sql/query`（`app/api/nl2sql.py`），传入 `user_id`、`session_id` 和自然语言 `question`；  
     - API 层将请求交给 `NL2SQLService.query(...)`。  
   - **服务与链路调用**：  
     1. `NL2SQLService` 先使用 `ConversationManager` 记录用户问题，并打 `NL2SQL_QUERY_COUNT` 指标；  
     2. 调用 `NL2SQLChain.generate_sql(question, user_id)` 生成候选 SQL：  
        - （可选）使用 LangChain LLM 做问题规划 `_plan`；  
        - 调用 `NL2SQLRAGService.retrieve(...)` 在三个命名空间联合检索上下文片段；  
        - 通过 `PromptBuilder` + `PromptTemplateRegistry(scene="nl2sql")` 构建 Prompt；  
        - 使用 LangChain ChatOpenAI（或回退到 `VLLMHttpClient`）生成 SQL，并经过 `SQLValidator` 校验与（可选）自我修正 `_refine_sql`；  
     3. 若最终 SQL 非空，则由 `SQLExecutor.execute(sql)` 在业务数据库中执行查询，异常时记录 `NL2SQL_QUERY_ERROR_COUNT` 与错误信息到会话；  
     4. 无论 SQL 是否执行成功，均会将最终 SQL 文本追加到会话中，便于后续审计与分析。  
   - **关键配置与依赖**：  
     - 大模型：`LLM_DEFAULT_MODEL` / `LLM_DEFAULT_ENDPOINT` / `LLM_DEFAULT_API_KEY`（控制 LangChain ChatOpenAI 与 vLLM 客户端）；  
     - 数据库：同上 `DatabaseConfig`；  
     - RAG：依赖前述 NL2SQL 命名空间已完成摄入；  
     - 安全策略：可在 `SQLValidator` 中扩展更多规则。  

**一句话小结**：  
- 对于使用方来说，NL2SQL 的主要操作路径是：**先通过 NL2SQLRAGService（或 RAG 管理接口）填充 Schema/业务知识/示例问答三类命名空间 → 再通过 `/nl2sql/query` 以自然语言发起查询，由系统自动完成 RAG 检索 + Prompt 编排 + LLM 生成 + SQL 校验与执行**。 

> **典型调用链总览**  
> - **知识摄入（推荐方式）**：  
>   后台任务或管理脚本 → `SchemaMetadataService` / 业务 ETL → `NL2SQLRAGService.index_*` → `RAGService.index_texts(..., namespace=...)` → `VectorStoreProvider`（向量库）。  
>   如需通过 HTTP 统一管理，也可以配合 `/rag/ingest/texts` 接口，将 NL2SQL 相关片段以合适的 `namespace` 摄入。  
> - **自然语言查询**：  
>   `POST /nl2sql/query` → `NL2SQLService.query` → `NL2SQLChain.generate_sql`（内部：SchemaMetadataService + NL2SQLRAGService + PromptBuilder + LLM + SQLValidator）→ `SQLExecutor.execute` → 返回 `rows`。

### 1.2 能力一览表

| 能力 | 说明 |
|------|------|
| **Schema 元数据管理** | `SchemaMetadataService` 内存维护 `TableSchema` 映射，可从真实 DB 反射刷新，也内置一套 Demo Schema 便于本地调试。 |
| **NL2SQL 专用 RAG** | `NL2SQLRAGService` 使用 `RetrievalPolicy` 统一路由，在命名空间 `nl2sql_schema` / `nl2sql_biz_knowledge` / `nl2sql_qa_examples` 上做向量+图事实联合检索，并合并去重结果。 |
| **Prompt 编排** | `PromptBuilder` 按 NL2SQL 设计文档，将 Schema 片段、业务知识与示例拼装成结构化 Prompt，结合 `PromptTemplateRegistry` 中 scene=`nl2sql` 的模板。 |
| **SQL 生成链路** | `NL2SQLChain` 将问题 →（可选）规划 `_plan` → RAG 检索 → Prompt 构建 → LLM 生成 SQL → 安全校验与自我修正。 |
| **SQL 校验与执行** | `SQLValidator` 确保仅包含安全 SELECT 语句；`SQLExecutor` 基于 SQLAlchemy AsyncEngine 执行只读 SQL 并返回行列表。 |
| **服务层与 API** | `NL2SQLService` 管理链路调用、执行、会话记录与指标；`/nl2sql/query` 作为统一 HTTP 入口。 |
| **监控与可观测性** | 指标 `NL2SQL_QUERY_COUNT` / `NL2SQL_QUERY_ERROR_COUNT`，以及可选 `LangSmithTracker`（在 NL2SQL 链路中埋点）。 |

### 1.3 逻辑架构图（组件关系）

```mermaid
flowchart TB
    subgraph API["API 层"]
        NL2SQLAPI["/nl2sql/query"]
    end

    subgraph Service["Service 层"]
        NService["NL2SQLService"]
    end

    subgraph Chain["NL2SQL 智能层"]
        NChain["NL2SQLChain"]
        RAG["NL2SQLRAGService"]
        PB["PromptBuilder"]
        LLM["LangChain ChatOpenAI / VLLMHttpClient"]
        VAL["SQLValidator"]
    end

    subgraph Data["数据访问 / 元数据"]
        SchemaSvc["SchemaMetadataService"]
        Exec["SQLExecutor"]
        DB[("业务数据库")]
    end

    subgraph Shared["共享能力"]
        Conv["ConversationManager"]
        RAGBase["RAGService + VectorStoreProvider"]
    end

    NL2SQLAPI --> NService
    NService --> NChain
    NService --> Exec
    NService --> Conv

    NChain --> SchemaSvc
    NChain --> RAG
    NChain --> PB
    NChain --> LLM
    NChain --> VAL

    RAG --> RAGBase
    Exec --> DB
```

### 1.4 时序图（从问题到结果）

```mermaid
sequenceDiagram
    participant Client
    participant API as /nl2sql/query
    participant Svc as NL2SQLService
    participant Chain as NL2SQLChain
    participant RAG as NL2SQLRAGService
    participant RBase as RAGService
    participant PB as PromptBuilder
    participant LLM as LLM (LangChain/VLLM)
    participant Val as SQLValidator
    participant Exec as SQLExecutor
    participant DB as Database

    Client->>API: POST /nl2sql/query (question, user_id, session_id)
    API->>Svc: NL2SQLQueryRequest
    Svc->>Svc: 记录用户问题到 ConversationManager
    Svc->>Chain: generate_sql(question, user_id)

    alt LangChain 可用
        Chain->>LLM: _plan(question) (可选规划)
        LLM-->>Chain: plan_summary
    end

    Chain->>RAG: retrieve(rag_query)
    RAG->>RBase: retrieve_context(..., namespace=nl2sql_schema/biz/qa)
    RBase-->>RAG: snippets
    RAG-->>Chain: merged_snippets

    Chain->>PB: build(question, snippets, system_prefix)
    PB-->>Chain: prompt

    alt LangChain ChatOpenAI 可用
        Chain->>LLM: _generate_via_langchain(prompt)
    else
        Chain->>LLM: VLLMHttpClient.generate(prompt)
    end
    LLM-->>Chain: sql

    Chain->>Val: validate(sql)
    alt validate 失败 且 LangChain 可用
        Chain->>LLM: _refine_sql(question, original_sql)
        LLM-->>Chain: refined_sql
        Chain->>Val: validate(refined_sql)
    end
    Chain-->>Svc: final_sql (possibly empty)

    alt final_sql 非空
        Svc->>Exec: execute(sql)
        Exec->>DB: SELECT ...
        DB-->>Exec: rows
        Exec-->>Svc: rows
    else
        Svc->>Svc: 不执行 SQL，rows=[]
    end

    Svc->>Svc: 将 SQL/错误摘要写入 ConversationManager
    Svc-->>API: NL2SQLQueryResponse(sql, rows)
    API-->>Client: JSON 响应
```

---

## 2. 模块与文件映射

> 按“接入层 → 服务层 → 智能层 → 元数据/RAG → 数据访问 → 公共能力”顺序列出。

| 模块 | 路径 | 职责 |
|------|------|------|
| 接入 API | `app/api/nl2sql.py` | 暴露 `POST /nl2sql/query` 接口，调用 `NL2SQLService`。 |
| 服务层 | `app/services/nl2sql_service.py` | 组合 `NL2SQLChain` + `SQLExecutor` + `ConversationManager` + Prometheus 指标，提供面向 API 的 `query` 方法。 |
| NL2SQL 链路 | `app/nl2sql/chain.py` | 实现“规划（可选）→ RAG → Prompt → LLM 生成 → SQL 校验/修正”的完整 NL2SQL 流程。 |
| Schema 元数据 | `app/nl2sql/schema_service.py` | 维护内存中的 `TableSchema` 映射；支持从真实数据库反射刷新 Schema；提供 Demo Schema。 |
| NL2SQL 专用 RAG | `app/nl2sql/rag_service.py` | 使用 `RAGService` 在 `nl2sql_schema` / `nl2sql_biz_knowledge` / `nl2sql_qa_examples` 命名空间上做多命名空间联合检索。 |
| Prompt 构建器 | `app/nl2sql/prompt_builder.py` | 按 NL2SQL 设计，将问题、Schema 片段与业务知识拼为结构化 Prompt；对接 `PromptTemplateRegistry`。 |
| SQL 校验 | `app/nl2sql/validator.py` | 只读 SQL 校验（确保仅 SELECT 等安全语句）。 |
| SQL 执行 | `app/nl2sql/executor.py` | 基于 SQLAlchemy AsyncEngine 执行只读 SQL，并返回行列表。 |
| 请求/响应模型 | `app/models/nl2sql.py` | `NL2SQLQueryRequest` / `NL2SQLQueryResponse` Pydantic 模型。 |
| 共享配置 | `app/core/config.py` | `AppConfig.llm`（大模型 endpoint 等）；`DatabaseConfig`（`DB_URL` 等）被 `SchemaMetadataService` / `SQLExecutor` 使用。 |
| 会话与指标 | `app/conversation/manager.py`、`app/core/metrics.py` | 会话记录与 Prometheus 指标（`NL2SQL_QUERY_COUNT` / `NL2SQL_QUERY_ERROR_COUNT`）。 |

---

## 3. 详细说明

本章按数据流顺序建议阅读：**Schema 元数据（3.1）→ RAG（3.2）→ Prompt（3.3）→ LLM 调用（3.4）→ SQL 校验与执行（3.5）→ 服务层与会话（3.6）**。

### 3.1 Schema 元数据服务（SchemaMetadataService）

- 文件：`app/nl2sql/schema_service.py`  
- 职责：
  - 在内存中维护 `TableSchema` 映射（表名、列、类型、注释）；  
  - 提供 `list_tables()` / `add_table()` 等接口；  
  - 通过 `refresh_from_db()` 使用 SQLAlchemy 反射真实数据库 Schema。
- 特性：
  - 默认加载一套 Demo Schema（`orders` 表）便于在无 DB 情况本地调试；  
  - 从真实 DB 拉取 Schema 时，使用 `DatabaseConfig.url` 连接，并在日志中记录刷新完成的表列表。

### 3.2 NL2SQL 专用 RAG（NL2SQLRAGService）

- 文件：`app/nl2sql/rag_service.py`  
- 命名空间设计：
  - `NS_SCHEMA = "nl2sql_schema"`：表/字段结构说明片段；  
  - `NS_BIZ = "nl2sql_biz_knowledge"`：业务规则、口径说明等；  
  - `NS_QA = "nl2sql_qa_examples"`：高质量 NL2SQL 问答样例。
- 摄入接口：

```python
index_schema_snippets(snippets: List[str])
index_biz_knowledge(snippets: List[str])
index_qa_examples(snippets: List[str])
```

- 检索接口：
  - `retrieve(question: str, top_k: int | None = None) -> List[str]`：
    - 针对同一个问题，在上述三个命名空间分别调用 `RAGService.retrieve_context(...)`；  
    - 将结果合并后做简单去重，返回供 Prompt 编排使用。

> 说明：当前实现使用相同的 `question` 作为检索向量，可在后续根据 `_plan` 输出对检索 query 做进一步增强。

### 3.3 Prompt 编排与模板（PromptBuilder + PromptTemplateRegistry）

- 文件：`app/nl2sql/prompt_builder.py` + `app/llm/prompt_registry.py`  
- 职责：
  - 根据问题、RAG 检索到的 Schema/业务/示例片段，以及 Prompt 模板（scene=`nl2sql`），构造最终送入 LLM 的 Prompt 文本；
  - 结构与 `docs/NL2SQL系统概要设计.md` 中的 Prompt 设计一致（System Prompt + RAG 片段 + User 问题）。
- 行为：
  - 通过 `PromptTemplateRegistry` 读取 scene=`nl2sql` 的模板文本（system_prefix），再由 `PromptBuilder.build(question, schema_snippets, system_prefix)` 组装；
  - 便于未来做 Prompt 版本化与 A/B 测试。

### 3.4 NL2SQLChain：规划 + RAG + LLM 生成

- 文件：`app/nl2sql/chain.py`  
- 构造函数依赖：
  - `SchemaMetadataService`、`NL2SQLRAGService`、`PromptBuilder`、`VLLMHttpClient`、`SQLValidator`、`PromptTemplateRegistry`；
  - 可选 `LangChain ChatOpenAI` 与 `LangSmithTracker`。
- 生成 SQL 主流程（`generate_sql(question, user_id)`）：
  1. **（可选）问题规划 `_plan`**  
     若 LangChain 可用，调用 `_plan(question)`：  
     - 通过 LLM 概括可能涉及的业务实体/表、关键字段、是否需要 join/聚合等；  
     - 将 summary 写入日志，便于调试与后续评估。
  2. **NL2SQL 专用 RAG 检索**  
     - 若有规划结果，则将 `plan_summary` 与原问题组合为 `rag_query`；  
     - 调用 `NL2SQLRAGService.retrieve(rag_query)`，由统一策略层决策后从 schema/biz/qa 三个命名空间联合检索上下文片段。
  3. **Prompt 构建**  
     - 通过 `PromptTemplateRegistry` 取 scene=`nl2sql` 模板；  
     - 调用 `PromptBuilder.build(question, schema_snippets, system_prefix)` 生成最终 Prompt。
  4. **SQL 生成**  
     - 若 LangChain ChatOpenAI 可用 → `_generate_via_langchain(prompt)`；  
     - 否则 → `VLLMHttpClient.generate(prompt)`；  
     - 得到初稿 SQL。
  5. **SQL 校验与自我修正**  
     - 调用 `SQLValidator.validate(sql)` 校验是否符合安全只读要求；  
     - 若未通过且 LangChain 可用 → `_refine_sql(question, original_sql)` 再请求一次 LLM 自我修正；  
     - 若仍未通过或无法修正，则返回空字符串。
  6. **LangSmith 埋点（可选）**  
     - 若启用 LangSmith，则通过 `LangSmithTracker.log_run` 记录一条 NL2SQL 运行记录，包含问题与生成 SQL。

### 3.5 SQLValidator 与 SQLExecutor

- 文件：`app/nl2sql/validator.py`、`app/nl2sql/executor.py`  
- `SQLValidator`：
  - 当前骨架版主要关注「只读」约束，禁止 DROP/DELETE/UPDATE/INSERT 等危险语句；  
  - 可按业务需求扩展更多规则（如禁止访问特定表、限制结果集大小等）。
- `SQLExecutor`：
  - 使用 `DatabaseConfig.url` 创建 SQLAlchemy AsyncEngine；  
  - 只执行 SELECT 类语句，并将结果转为 `List[dict[str, Any]]`；  
  - 在执行前后打 debug 日志，便于排查与审计。

### 3.6 服务层与会话（NL2SQLService + ConversationManager）

- 文件：`app/services/nl2sql_service.py`、`app/conversation/manager.py`  
- 行为：
  - 在 `query` 开始时，将用户问题写入会话（方便后续回放与分析）；  
  - 调用 `NL2SQLChain.generate_sql(...)` 生成 SQL；  
  - 若 SQL 非空，则通过 `SQLExecutor.execute` 执行并捕获异常：  
    - 执行失败则增加 `NL2SQL_QUERY_ERROR_COUNT` 并将错误摘要写入会话；  
  - 不论执行成功与否，最后将 SQL 文本写入会话（用于记录用户交互中“模型给出的 SQL”）；  
  - 返回 `NL2SQLQueryResponse(sql, rows)`。

---

## 4. 配置与环境变量

### 4.1 大模型与 NL2SQL 相关配置

- **`AppConfig.llm`**（`app/core/config.py`）：  
  - `LLM_DEFAULT_MODEL`：默认逻辑模型 ID；  
  - `LLM_DEFAULT_ENDPOINT`：vLLM 或其他 OpenAI 兼容服务地址；  
  - `LLM_DEFAULT_API_KEY`：大模型 API Key。
- 这些配置被 `VLLMHttpClient` 与 `NL2SQLChain` 的 LangChain ChatOpenAI 初始化使用。

### 4.2 数据库配置（DatabaseConfig）

- 环境变量（配合 `DatabaseConfig` 使用）：

| 变量 | 说明 | 默认（示例） |
|------|------|-------------|
| `DB_USER` / `DB_PASSWORD` | DB 用户/密码 | `root` / `1qaz@4321` |
| `DB_HOST` | DB 主机 | `124.222.37.179` |
| `DB_NAME` | 默认数据库名 | `aishare` |
| `DB_URL` | 完整连接串（如 `mysql+aiomysql://user:pwd@host/db`） | 若未指定，则由上述字段拼接 |

- `SchemaMetadataService.refresh_from_db()` 与 `SQLExecutor` 均通过 `get_app_config().db` 获取连接信息。

---

## 5. HTTP API（NL2SQL 管理）

- **`POST /nl2sql/query`**（`app/api/nl2sql.py`）  
  - Request：`NL2SQLQueryRequest`（`user_id`、`session_id`、`question`）。  
  - Response：`NL2SQLQueryResponse`（`sql`、`rows`）。  
  - 行为：调用 `NL2SQLService.query` 执行完整 NL2SQL 流程。

---

## 6. 典型调用链小结

高层视角（简化）：

```mermaid
flowchart LR
    Client --> API["/nl2sql/query"]
    API --> Svc["NL2SQLService"]
    Svc --> Chain["NL2SQLChain"]
    Chain --> RAG["NL2SQLRAGService"]
    RAG --> RBase["RAGService"]
    Chain --> Exec["SQLExecutor"]
    Exec --> DB[("Database")]
    Svc --> Conv["ConversationManager"]
```

---

## 7. 与通用 RAG / GraphRAG 的关系

- NL2SQLRAGService 复用 **通用 RAG 基座**（`RetrievalPolicy` + `RAGService` + `VectorStoreProvider` + 可选 `GraphQueryService`）：
  - 通过 `namespace` 将 NL2SQL 的 Schema / 业务知识 / Q&A 与其他 RAG 场景隔离；  
  - 在未来切换为 HybridRAGService 时，可通过配置替换底层 `RAGService` 实例，而不影响 NL2SQL 代码。
- 当前 NL2SQLRAGService 已可按统一策略层决策接入图事实召回（可选）；  
  - 进一步演进方向是将 Schema 元数据结构化图谱化（Schema GraphRAG），用于更强的跨表关系推理。

---

## 8. 后续演进建议

结合 `docs/NL2SQL系统概要设计.md`，当前 NL2SQL 实现仍是**企业级骨架**，后续可从以下方向演进：

1. **增强 RAG 与 Schema 语义**：  
   - 将 `SchemaMetadataService` 的结构化信息（表/列/约束）系统性转化为 RAG 文本片段，完善 `nl2sql_schema` 命名空间；  
   - 为 `nl2sql_biz_knowledge` 与 `nl2sql_qa_examples` 设计管理接口与数据填充流程。
2. **细化 Prompt 策略**：  
   - 将不同业务域（如订单、用户、财务）的 NL2SQL Prompt 版本化，并与 `PromptTemplateRegistry` 集成 A/B 测试；  
   - 针对多表复杂问题，引入“显式规划 + 显式 Thought 输出 + SQL 生成”组合策略，提升可解释性。
3. **规划与自我修正增强**：  
   - 在 `_plan` 中返回更结构化的规划结果，并用于优化 RAG 检索 query；  
   - 在 `_refine_sql` 中加入执行错误信息作为上下文（例如语法错误信息、权限错误），实现针对性的自我修正。
4. **安全与审计**：  
   - 扩展 `SQLValidator` 的规则集，支持表级/字段级权限与资源约束；  
   - 为 SQL 执行增加审计日志与“干跑（dry-run）”模式等能力。
5. **GraphRAG 结合**：  
   - 在后续版本中，将数据库 Schema 映射为图结构（表/列为节点、外键/业务关系为边），在 NL2SQL 中引入 GraphRAG，改善跨表/复杂 join 推理能力。

---

*若对上述实现有修改（尤其是 `app/nl2sql/*`、`app/core/config.py` 中与 NL2SQL 相关的配置），请同步更新本说明文档。*

