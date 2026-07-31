# 系统执行轨迹与 LangSmith 观测改造方案

本文档给出本仓库 **本地执行轨迹（Execution Trace）+ 运维 traces API**、**请求级链路可视化（OTLP → Tempo → Grafana）** 与 **LangSmith 外部观测** 的统一改造方案，目标是让主要在线业务具备与综合分析相近的「可回查编排过程」能力：专网可查本地 Store / Grafana Trace；可选外网时再镜像到 LangSmith。

**本期实现范围（摘要）**：统一本地 Trace 模型与 Store/API → **在线编排业务 + 任务类（RAG 摄入 / 检修提取 / GraphRAG 重建等）埋点** → **同源导出 OTLP 至 Tempo，在 Grafana 用 Trace / Node Graph 查看请求级或任务级执行 DAG** → LangSmith 可选增强。静态 StateGraph 设计图仍放文档 Mermaid，**不**纳入 Grafana。

**关联文档 / 代码**

| 文档 / 代码 | 说明 |
|-------------|------|
| `app/models/analysis.py` | `AnalysisTrace` / `AnalysisTraceView` 标杆模型 |
| `app/services/analysis_trace_store.py` | 内存 / Redis / ES 归档与工厂 |
| `app/api/analysis.py` | `GET /analysis/traces*` 运维族 |
| `app/services/analysis_agent_service.py` | 轻量进程内 `_trace_store` |
| `app/llm/langsmith_tracker.py` | 现有可选 `create_run` 薄封装 |
| `app/llm/graphs/chatbot_graph_runner.py` | 客服图编排 + 流式 finally LangSmith |
| `app/nl2sql/chain.py` | NL2SQL 多点 `log_run` |
| `app/api/rag_admin.py` / `app/rag/ingestion_orchestrator.py` | RAG 异步摄入 Job（`/rag/jobs*`） |
| `app/api/inspection_extract*.py` | 检修提取异步 Job（`/inspection-extract/jobs*`） |
| `docs/系统Prometheus资源监控实现方案.md` | 指标层（与 Trace 互补，不互相替代） |
| `monitoring-deploy/` | Prometheus / Grafana 部署；**本期扩展 Tempo** |
| `configs/monitoring/analysis-trace-alert-rules.yml` | Analysis Trace 相关告警样例 |

---

## 1. 背景与现状结论

### 1.1 要解决什么

| 诉求 | 说明 |
|------|------|
| **排障** | 给定 `request_id`（同步）或 `job_id`（异步），回放「走过哪些节点/阶段、耗时、成功/跳过/失败、降级原因」 |
| **请求级 / 任务级链路可视化** | 在 Grafana 中按 ID 查看执行瀑布图 / Node Graph（动态 DAG），涵盖在线编排与长任务流水线 |
| **运维聚合** | 列表、按类型/模式/任务类型统计、趋势、降级 TopN（不必打开 LangSmith） |
| **效果分析** | Prompt/模型/RAG/SQL 质量对照（可走 LangSmith UI 或导出） |
| **专网可用** | 内网无外网时仍可本地查 Trace + 自建 Tempo/Grafana；LangSmith 保持可选、失败 no-op |

### 1.2 现状分层

| 层级 | 综合分析 `/analysis` | analysis_agent | Chatbot / NL2SQL / LLM infer | 异步 Job（检修/RAG 摄入等） |
|------|----------------------|----------------|------------------------------|-----------------------------|
| 节点级执行轨迹 | **有** `AnalysisTrace` | 结果内 `slot_trace` 等 | **无**统一存储；仅 SSE/`meta` 摘要 | Job 状态/分块；**缺统一阶段时间线**（本期补齐） |
| 持久化 | Redis/ES/memory（`ANALYSIS_TRACE_*`） | **进程内 dict** | 无 | Job store（各模块自建）；Trace 本期写入统一 Store |
| 运维 API | **完整** `/analysis/traces*` | 仅 `GET .../trace/{id}` | **无** | **有** `/…/jobs/{job_id}`；本期增 `/…/traces/{job_id}` |
| LangSmith | **基本未接线** | 未接线 | 有手动 `log_run` | 未统一；本期可选 |

### 1.3 关键结论

1. **本地 Trace 标杆仅综合分析**；analysis_agent 有「按 ID 查」形态但存储弱；其它在线编排业务 **不对齐**。
2. **异步 Job**（RAG 摄入、检修提取等）已有 **状态机 / 分块进度 API**，但缺少与编排 Trace 同构的「阶段时间线」，难以在 Grafana 用同一套 Node Graph 排障。
3. **LangSmith 已有薄封装**，覆盖客服/NL2SQL/通用推理的摘要 run，**不是**嵌套子 run 树，且 **不覆盖综合分析主路径与任务类**。
4. Prometheus 解决「量与延迟」；**不能替代**按 ID 回查编排/流水线过程。请求/任务级 DAG 可视化应走 **Tempo + Grafana**，与 Prom 看板分层。
5. 改造原则：**统一本地 Trace 模型（同步请求 + 异步任务同构）与存储并埋点；同源导出 OTLP（本期）供 Grafana 看执行 DAG；现有 Job API 保留；LangSmith 作为可选第三通道，失败不影响主流程。**

### 1.4 本方案边界

**做（含本期请求/任务级链路可视化）：**

- 统一 `ExecutionTrace` 核心模型 + 通用 Store + 统一运维 API 前缀（`kind=request|job`）
- **在线编排覆盖**：综合分析（迁移/兼容）、analysis_agent、chatbot、nl2sql、llm infer
- **任务类覆盖（本期）**：RAG 摄入、检修提取（含 V0 若仍在用）、GraphRAG 重建等长任务流水线阶段 Trace
- **请求级 / 任务级链路**：`ExecutionTraceRecord` → OTLP 导出 → **Grafana Tempo** → Grafana Explore / Trace / **Node Graph**
- `monitoring-deploy` **本期**增补 Tempo 与 Grafana Tempo 数据源（可 profile 可选启动）
- 强化 `LangSmithTracker`（父子 run、`request_id`/`job_id` 关联、脱敏、异步非阻塞）
- 与现有 `ANALYSIS_TRACE_*`、现有 `/rag/jobs*` / `/inspection-extract/jobs*` **平滑兼容**（Job API 不废止）

**不做（本期明确排除）：**

- **静态** StateGraph / 编排设计图进 Grafana（继续用文档 Mermaid / 代码）
- 自研业务前端 DAG 画布（可用 Grafana Trace UI 替代本期诉求）
- 仅靠 Infinity/JSON 硬拼 Node Graph 作为主路径（仅作无 Tempo 时的临时备选，不作为本期交付标准）
- 用 Trace **替代** Job 状态机（取消、重试、文档列表、分块结果等仍走现有 Job API）
- 人脸识别、小模型训练等与「编排/摄入流水线」无关的通道（可后续按需挂 `module`）
- 强制现场开通外网 LangSmith（专网默认关；Tempo 走内网即可）
- 用 LangSmith 或 Tempo **替代**本地归档作为唯一真相源（本地 Store 仍为专网保底）

---

## 2. 目标架构

```text
                  ┌─────────────────────────────────────┐
  业务 Runner /   │  TraceRecorder（统一埋点 Facade）     │
  Job Orchestrator│  - start(request|job) / node|stage   │
  (analysis /     │  - set_degrade / checkpoint/finalize │
   chatbot /      └──────────────┬──────────────────────┘
   rag ingest / …)               │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
ExecutionTraceStore      Prometheus metrics         可选镜像通道
(memory/redis/es)        (量 / 延迟 / 降级)     ┌────────┴────────┐
        │                                      ▼                 ▼
        │                              OtlpTraceExporter   LangSmithTracker
        │                              (本期，可采样)      (可选 create_run)
        │                                      │
        ▼                                      ▼
 GET /ops/traces*                         Grafana Tempo
 GET /{module}/traces/{id}                     │
 （含 /rag/traces/{job_id} 等）           Grafana Explore
                                    Trace 瀑布图 / Node Graph
                                    （请求级 / 任务级执行 DAG）
```

**多通道语义（本期）：**

| 通道 | 职责 | 失败策略 |
|------|------|----------|
| 本地 Store | 运维 HTTP 回查、列表统计、专网保底排障 | 写失败打日志 + 指标；业务/Job 仍正常推进 |
| **OTLP → Tempo → Grafana** | **请求级 / 任务级执行链路可视化**（瀑布图 / Node Graph） | 导出失败 no-op；不影响主流程与本地 Store |
| LangSmith | 研发调试、Prompt/效果实验（可选外网；任务类默认可关） | no-op / warning；永不阻断 |
| Prometheus | 实时量与延迟、节点/阶段健康趋势 | 已有路径，Trace 改造不替代 |
| **现有 Job API** | 取消、重试、文档列表、分块结果、状态机字段 | **保留**；与 Trace 互补，不互相替代 |

---

## 3. 统一数据模型（本地 Trace）

### 3.1 核心：`ExecutionTraceRecord`

建议新建 `app/models/execution_trace.py`（名称可微调），与现有 `AnalysisTrace` **兼容映射**。

```text
ExecutionTraceRecord
├── request_id: str                 # 全局唯一；同步=业务 request_id；异步任务建议 = job_id
├── kind: request | job             # 本期：区分在线请求与长任务
├── module: str                     # analysis | chatbot | rag_ingest | inspection_extract | graph_rebuild | ...
├── scene: str | null               # 如 analysis_type / intent_label / ingest_mode / rebuild_scope
├── user_id / session_id: optional  # 任务类可空或填提交者
├── status: success | partial | failed | aborted | running  # job 运行中可 checkpoint 为 running
├── started_at / finished_at: ISO8601  # running 时 finished_at 可空
├── total_latency_ms: int | null    # 未结束可空或用 wall 至今
├── nodes: List[TraceNode]          # 有序节点（图节点或流水线阶段）
├── degrade_reasons: List[str]
├── summary: str | null             # 短摘要（列表预览用）
├── meta: Dict                      # 模块扩展（勿放大原文）；任务类含 job_id、retry_of、doc_count 等
└── payload_ref: optional           # 大结果不进 Trace：仅引用 job_id / 对象键
```

```text
TraceNode
├── node_id: str                    # 图节点名或阶段名：retrieve / chunk / embed / parse_table …
├── status: success | failed | skipped | running
├── latency_ms: int | null
├── started_at / finished_at: optional
├── error: str | null               # 截断
└── attributes: Dict                # 小字段：prompt_variant、hit_count、sql_hash、cache_hit、
                                    # doc_name、chunk_count、stage_durations 等
```

**ID 约定（强制）：**

| kind | `request_id`（Store / API / OTLP 属性主键） | 说明 |
|------|---------------------------------------------|------|
| `request` | 业务响应 / SSE `meta.request_id` | 与现网一致 |
| `job` | **等于** `job_id`（提交接口返回值） | `meta.job_id` 冗余同值；重试新任务用**新** `job_id`，`meta.retry_of` 指原任务 |

### 3.2 与现有 `AnalysisTrace` 的关系

| 现有字段 | 映射 |
|----------|------|
| `node_latency_ms` / `node_status` | → `nodes[]` |
| `degrade_reasons` | 原样 |
| `data_plan_trace` / `template_versions` / `execution_summary` | → `meta.data_plan_trace` 等，或一期保留 `AnalysisV2Result.trace` 并存 |
| `AnalysisV2Result` 全文 | **继续**可按现网存「结果归档」；运维列表默认只读 `ExecutionTraceRecord` + 可选 `include_result=1` 拉全文 |

**兼容策略（推荐）：**

1. **一期**：综合分析继续写 `AnalysisV2Result` 到现有 store；同时 **投影** 一份 `ExecutionTraceRecord` 写入统一 Store（或同一 Redis 前缀不同 key）。
2. **二期**：统一 Store 成为唯一后端；`/analysis/traces*` 改为读统一模型 + 兼容视图适配器。
3. analysis_agent：废弃进程内 dict，改为统一 Store；API 路径可保留 `/analysis-agent/trace/{id}` 作别名。

### 3.3 脱敏与体积

强制规则（写入 Store / OTLP / LangSmith 前统一走 `TraceSanitizer`）：

| 规则 | 说明 |
|------|------|
| 截断 | query / answer / sql 原文最长 N 字符（配置，如 2KB） |
| 哈希 | 完整 SQL / prompt 可存 `sha256` 便于对照，不存全文到列表索引 |
| 禁止 | Token、Cookie、私钥、完整证件号等 |
| 大对象 | 报告全文、检索 chunks / 摄入文档正文 → `payload_ref`，详情走 Job/业务 API；**OTLP Span 禁止大对象** |

---

## 4. 统一存储与配置

### 4.1 抽象

从 `AnalysisTraceStore` **抽取**通用接口（可放 `app/services/execution_trace_store.py`）：

```text
ExecutionTraceStore
  save(record: ExecutionTraceRecord) -> None
  get(request_id: str) -> Optional[ExecutionTraceRecord]
  list(limit, offset, *, module?, kind?, scene?, status?, score_min_ms?, ...) -> (rows, total)
  # 可选二期：stats / trend / degrade_topn 下沉到 store 或 Service 聚合
```

实现复用现有三种后端思路：

| Backend | 用途 | Key 建议 |
|---------|------|----------|
| `memory` | 单机开发 | LRU `max_items` |
| `redis` | **现场默认推荐** | `exec:trace:{request_id}` + ZSET 索引；按 `module` / `kind` 二级索引 |
| `es` | 长期归档/检索 | index 如 `execution_trace_archive` |

综合分析现有 `analysis:trace:*` **一期保留**；投影双写时注意 TTL 一致。

### 4.2 配置项（建议）

环境变量前缀 `EXECUTION_TRACE_*`（与 `ANALYSIS_TRACE_*` 并行一段时间）：

| 变量 | 含义 | 建议默认 |
|------|------|----------|
| `EXECUTION_TRACE_ENABLED` | 总开关 | `true` |
| `EXECUTION_TRACE_BACKEND` | `memory` \| `redis` \| `es` | **推荐 `redis`**；未设置时有 `REDIS_URL` 则自动 `redis`；`es` 未实现会降级 |
| `EXECUTION_TRACE_TTL_MINUTES` | TTL | `1440` |
| `EXECUTION_TRACE_MAX_ITEMS` | 索引裁剪 | `10000` |
| `EXECUTION_TRACE_MODULES` | 逗号名单，空=本期全部模块 | `analysis,analysis_agent,chatbot,nl2sql,llm_infer,rag_ingest,inspection_extract,graph_rebuild` |
| `EXECUTION_TRACE_QUERY_MAX_CHARS` | 原文截断 | `2048` |
| `ANALYSIS_TRACE_DUAL_WRITE` | analysis 是否投影到统一 Store | 一期 `true` |

OTLP / Tempo 相关变量见 **§8.3**（`EXECUTION_TRACE_OTLP_ENABLED`、`OTEL_*`）。

**兼容：** `ANALYSIS_TRACE_*` 继续驱动现有 `/analysis/traces*` 数据源，直到后期切换完成；文档与 `.env.example` 标明迁移时间表。

---

## 5. 统一运维 API

### 5.1 推荐统一前缀（新建）

挂载例如 `app/api/ops_traces.py` → `prefix=/ops`：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/ops/traces/{request_id}` | 详情（`ExecutionTraceRecord`） |
| GET | `/ops/traces` | 分页列表：`module`/`kind`/`scene`/`status`/时间窗 |
| GET | `/ops/traces/stats` | 按 module/kind/scene/status/degrade 聚合 |
| GET | `/ops/traces/trend` | 时间桶趋势 |
| GET | `/ops/traces/degrade-topn` | 降级 TopN |
| GET | `/ops/traces/{request_id}/result` | **可选**：拉取业务全文（仅 analysis 等有 payload 时） |

鉴权：与现有管理接口一致（内网 / 网关 / 后续统一 admin token）；**禁止**对公网匿名开放全文。

### 5.2 模块别名（兼容与体验）

| 别名 | 行为 |
|------|------|
| 现有 `GET /analysis/traces*` | 一期保持；内部可读统一 Store 或旧 Store |
| `GET /analysis-agent/trace/{id}` | 改为读统一 Store；补齐 list（可选） |
| **新增** `GET /chatbot/traces/{request_id}` | 别名 → `/ops/traces/{id}?module=chatbot` |
| **新增** `GET /nl2sql/traces/{request_id}` | 同上 |
| **新增** `GET /llm/traces/{request_id}` | 同上 |
| **新增** `GET /rag/traces/{job_id}` | 别名 → `/ops/traces/{id}?module=rag_ingest`；与 `/rag/jobs/{job_id}` 并存 |
| **新增** `GET /inspection-extract/traces/{job_id}` | 别名 → `module=inspection_extract`（V0 可同前缀或单独 module） |
| **新增** `GET /graph/traces/{job_id}`（若重建有 job） | 别名 → `module=graph_rebuild` |

SSE/同步响应 **必须** 稳定返回 `request_id`；异步提交响应 **必须** 稳定返回 `job_id`（即任务 Trace 的 `request_id`）。

### 5.3 响应约定

详情 200 示例字段（示意）：

```json
{
  "ok": true,
  "request_id": "...",
  "module": "chatbot",
  "scene": "kb_qa",
  "status": "success",
  "total_latency_ms": 1234,
  "nodes": [
    {"node_id": "intent", "status": "success", "latency_ms": 12},
    {"node_id": "retrieve", "status": "success", "latency_ms": 80, "attributes": {"hit_count": 5}},
    {"node_id": "answer", "status": "success", "latency_ms": 900}
  ],
  "degrade_reasons": [],
  "meta": {"prompt_variant": "...", "langsmith_run_id": "...", "tempo_trace_id": "..."}
}
```

任务类示例（RAG 摄入，`request_id` = `job_id`）：

```json
{
  "ok": true,
  "request_id": "a1b2c3d4-...",
  "kind": "job",
  "module": "rag_ingest",
  "scene": "async_ingest",
  "status": "success",
  "total_latency_ms": 58000,
  "nodes": [
    {"node_id": "validate", "status": "success", "latency_ms": 20},
    {"node_id": "parse", "status": "success", "latency_ms": 12000, "attributes": {"doc_count": 2}},
    {"node_id": "chunk", "status": "success", "latency_ms": 800, "attributes": {"chunk_count": 40}},
    {"node_id": "embed", "status": "success", "latency_ms": 30000},
    {"node_id": "index", "status": "success", "latency_ms": 5000}
  ],
  "degrade_reasons": [],
  "meta": {"job_id": "a1b2c3d4-...", "retry_of": null, "tempo_trace_id": "..."}
}
```

404：未找到或已 TTL 过期。详情中的 `tempo_trace_id` / `langsmith_run_id` 为可选回写字段，便于从 HTTP 跳转 Grafana / LangSmith。

---

## 6. 分模块埋点改造

### 6.1 通用埋点方式：`TraceRecorder`

建议 `app/observability/trace_recorder.py`：

```text
with TraceRecorder.start(module=..., request_id=..., kind="request"|"job", ...) as tr:
    with tr.node("intent"):  # 或流水线 stage 名
        ...
    tr.checkpoint()   # 任务类：阶段结束后可覆盖写 Store（status=running）
    tr.add_degrade("rag_empty")
# exit/finalize 时 save(store) + 可选 otlp.export(record) + 可选 langsmith.mirror(record)
```

LangGraph / Job 适配：

- 在各 `node` 或流水线阶段入口/出口计时；或
- 图 `ainvoke`/`astream` 外层包一层，用回调记录节点名与耗时（优先显式埋点，避免隐式漏字段）；
- Job Orchestrator 在阶段回调中调用同一 `TraceRecorder`（见 §6.7）。

`finalize` **本期**约定：本地 Store 成功写入后，再异步触发 OTLP 导出与 LangSmith 镜像（二者独立开关，互不阻塞）。任务类允许中途多次 `checkpoint`，**OTLP 默认仅在终态导出一次**（见 §8.2），避免长任务刷屏。

### 6.2 综合分析（标杆迁移）

| 项 | 动作 |
|----|------|
| 现状 | 已有完整 Trace + API |
| 一期 | 投影 `ExecutionTraceRecord`；`meta` 保留 `data_plan_trace` 等 |
| LangSmith | 在 finalize 时 `log_run`/`log_tree`：name=`analysis_v2`，metadata 含 `request_id`、`analysis_type`、`degrade_reasons`、节点耗时摘要 |
| API | 保持 `/analysis/traces*`；文档注明与 `/ops/traces` 等价关系 |

### 6.3 analysis_agent

| 项 | 动作 |
|----|------|
| 去掉 | 进程内 `_trace_store` 作为生产路径 |
| 写入 | `on_complete` → `ExecutionTraceStore.save`；`nodes` 可由 slot 顺序生成 |
| API | `GET /analysis-agent/trace/{id}` 读统一 Store；建议补 `GET /analysis-agent/traces` 列表 |
| LangSmith | `analysis_agent_run` 一条父 run + 每 slot 子 run（二期） |

### 6.4 智能客服（Chatbot）

| 项 | 动作 |
|----|------|
| 埋点位置 | `ChatbotLangGraphRunner.run_stream`：按图节点（intent / retrieve / nl2sql / answer / …）记 `TraceNode` |
| 持久化 | `finally` 中 `save`（成功/失败/abort 均落；abort 标 `aborted`） |
| 响应 | `finished.meta.request_id` 已有则复用；保证与 Store key 一致 |
| API | `GET /chatbot/traces/{request_id}` + 纳入 `/ops/traces?module=chatbot` |
| LangSmith | 将现有单次 `chatbot_langgraph_stream` 升级为：父 run + 关键子步骤；`extra.request_id` 必填 |

### 6.5 NL2SQL

| 项 | 动作 |
|----|------|
| 埋点 | `NL2SQLChain`：generate / validate / repair / execute（若有）节点 |
| 字段 | `attributes.sql_hash`、`cache_hit`、`dialect`、错误码；SQL 原文截断 |
| API | `GET /nl2sql/traces/{request_id}`；同步响应增加 `request_id`（若尚无） |
| LangSmith | 合并现有多处 `log_run` 为同一 `parent_run_id` 树，避免碎片 run |

### 6.6 通用 LLM `/llm/infer`

| 项 | 动作 |
|----|------|
| 埋点 | 单节点或 prompt_resolve → infer |
| meta | `model`、`prompt_version`、`used_rag` |
| API | `GET /llm/traces/{request_id}` |
| LangSmith | 保持 `llm_inference`，补齐 `request_id` 与截断策略 |

### 6.7 任务类（本期：RAG 摄入 / 检修提取 / GraphRAG 重建等）

> **原则**：与在线编排共用 `ExecutionTraceRecord`；`kind=job`，`request_id=job_id`。  
> **Job API 仍是状态机与业务结果入口**；Trace 提供**阶段时间线**，供 `/ops/traces` 与 Grafana Node Graph 统一排障。

#### 6.7.1 覆盖清单（本期）

| module | 现有入口 | 阶段节点（示意，实现时对齐代码真实步骤名） |
|--------|----------|---------------------------------------------|
| `rag_ingest` | `POST /rag/jobs/ingest`，编排 `IngestionOrchestrator` | `validate` → `parse`（含 MinerU 等）→ `chunk` → `embed` → `index` → `finalize`；可复用已有 `stage_durations_ms` |
| `inspection_extract` | `/inspection-extract/jobs*`（及 V0 若仍启用） | `submit` → `split_chunks` → `parse_chunk`（可多段并行，用 attributes 记 `work_idx`）→ `merge` → `finalize` |
| `graph_rebuild` | Graph 重建/运维长任务（若有 job_id） | `prepare` → `extract` → `write_graph` → `finalize`；低频，默认纳入白名单但可采样 |

同步写入接口（如 `POST /rag/ingest` 单文档同步）本期建议：`kind=request`，`module=rag_ingest`，`scene=sync_ingest`，节点可压缩为单段或短流水线。

#### 6.7.2 埋点与落库策略

| 项 | 约定 |
|----|------|
| 启动 | Job 进入 `RUNNING` 时 `TraceRecorder.start(kind=job, request_id=job_id, …)`，`status=running`，可先 `save` 一条骨架 |
| 阶段 | 每阶段结束 `tr.node(stage)` / 写入 `TraceNode`；RAG 侧优先挂钩现有 `_record_step_ms` / `stage_durations_ms` |
| 检查点 | **本期推荐**：每个阶段完成后 `checkpoint()` 覆盖写 Store（运维可看进行中任务）；避免仅终态才有 Trace |
| 终态 | success / failed / aborted（含取消）时 `finalize`：补全 `total_latency_ms`、触发 OTLP（见 §8）、可选 LangSmith |
| 重试 | `retry_job` 新 `job_id` → **新 Trace**；`meta.retry_of=原 job_id` |
| 多文档 | Root 下按文档再挂子节点 `doc:{name}`，或仅在 attributes 聚合 `doc_count`（避免 Span 爆炸；文档明细仍查 Job documents API） |

#### 6.7.3 API 与运维路径

| 能力 | 路径 |
|------|------|
| 任务状态 / 取消 / 重试 / 文档列表 | **现有** `/rag/jobs*`、`/inspection-extract/jobs*`（不变） |
| 阶段时间线 Trace | `/rag/traces/{job_id}`、`/inspection-extract/traces/{job_id}`、`/ops/traces?kind=job` |
| Grafana | 按 `request_id`（=job_id）或 `module=rag_ingest` 检索 Tempo |

#### 6.7.4 LangSmith（任务类）

- 默认：`LANGSMITH_MIRROR_MODULES` **可不含** `rag_ingest` 等（长任务噪声大、专网常关）。
- 若开启：仅终态 mirror 一条父 run + 阶段子 run；禁止上传文档原文。

#### 6.7.5 明确不做（任务类）

- 不把 chunk 全文、向量、表格单元格写入 Trace/Tempo。
- 不用 Trace 实现取消/重试语义。
- 不为每个人脸/小模型推理请求强制建 Job Trace。

---

## 7. LangSmith 改造方案

### 7.1 现状问题

- 仅 `Client.create_run` 扁平摘要，无父子关联、无稳定 `id` 回写本地。
- 模块覆盖不全（分析主路径缺失）。
- 每次业务类可能 `new LangSmithTracker()`，重复读环境与建 Client。
- 无脱敏、无批量/异步，高峰可能拖尾延迟（虽已吞异常）。

### 7.2 目标能力

| 能力 | 说明 |
|------|------|
| 开关 | `LANGSMITH_ENABLED` + Key + Project；专网默认关 |
| 单例 | 进程内共享 Client（懒加载） |
| 镜像 | `mirror_execution_trace(record)`：由本地 Record 生成 LangSmith runs |
| 父子 run | 父=`{module}_request`，子=`node:{node_id}`；`parent_run_id` 关联 |
| 回写 | 父 run id 写入 `record.meta.langsmith_run_id`（便于双向跳转） |
| 脱敏 | 与本地同一 `TraceSanitizer` |
| 非阻塞 | 默认线程池 / `asyncio.create_task` 投递；超时丢弃并打 metrics |

### 7.3 API 演进（`LangSmithTracker`）

建议保留 `log_run` 兼容，新增：

```text
start_parent(name, inputs, metadata) -> run_id | None
end_parent(run_id, outputs, error=None)
log_child(parent_run_id, name, run_type, inputs, outputs, metadata)
mirror_execution_trace(record: ExecutionTraceRecord) -> None
```

实现要点：

- 使用官方 SDK 支持的 `id` / `parent_run_id` / `start_time` / `end_time` / `error` 字段（以当前 `langsmith` 包版本为准，改造时对照 SDK）。
- `metadata` 固定带：`request_id`、`module`、`scene`、`app_env`、`git_sha`（若有）。
- 可选：设置 `LANGCHAIN_TRACING_V2` **不作为**本方案主路径（避免与手动 Tracker 双轨混乱）；若未来改用自动回调，需单独立项。

### 7.4 配置补充

| 变量 | 含义 | 建议 |
|------|------|------|
| `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | 现有 | 不变 |
| `LANGSMITH_ENABLED` | 显式 false 关闭 | 不变 |
| `LANGSMITH_ENDPOINT` | 自建/代理 LangSmith API | 可选 |
| `LANGSMITH_MIRROR_MODULES` | 镜像模块白名单 | 与 Trace 模块对齐 |
| `LANGSMITH_ASYNC` | 异步投递 | 默认 `true` |
| `LANGSMITH_SAMPLE_RATE` | 0~1 采样 | 生产可 `0.1~1.0` |

### 7.5 与本地 Trace / Tempo 的对应关系

```text
本地 request_id  ←──唯一业务键──→  运维 API / 日志 / Grafana 属性检索
       │
       ├── meta.tempo_trace_id ──→ Grafana Tempo Trace（本期请求级 DAG）
       └── meta.langsmith_run_id ──→ LangSmith UI（可选）
```

禁止仅依赖 LangSmith URL 作为现场排障入口；专网优先 **本地 API + Grafana/Tempo**。

---

## 8. 请求级 / 任务级链路可视化（本期实现：OTLP → Tempo → Grafana）

> **定位**：用 Grafana 查看**某次请求或某次异步任务的执行 DAG**（动态），不是静态编排设计图。  
> **本期交付标准**：应用侧同源导出 OTLP（含 `kind=job`）+ `monitoring-deploy` 内可选 Tempo + Grafana Tempo 数据源与操作说明；运维可用 `request_id`（任务场景即 `job_id`）在 Grafana Explore 打开瀑布图 / Node Graph。

### 8.1 为何不「只靠 Grafana + Prometheus」

| 能力 | Prometheus + Grafana | Tempo + Grafana Trace / Node Graph |
|------|----------------------|-------------------------------------|
| 节点/阶段 P95 / 错误率趋势 | **合适** | 辅助 |
| 单次 `request_id`/`job_id` 走过哪些节点、父子耗时 | 不合适 | **合适** |
| 静态 StateGraph 拓扑 | 不合适 | 不合适（继续 Mermaid） |

因此本期采用：**Prom 看健康，Tempo 看单次链路（请求或任务），本地 Store 作 HTTP 保底；Job API 看状态机与业务产物。**

### 8.2 数据流（与本地 Trace 同源）

```text
TraceRecorder.checkpoint(record)   # 任务类：仅 Store（可选）
TraceRecorder.finalize(record)     # 请求终态 / 任务终态
        │
        ├─► ExecutionTraceStore.save(record)          # 真相源 / HTTP API
        ├─► OtlpTraceExporter.export(record)          # 本期默认：仅 finalize
        │         │
        │         ▼
        │   OTLP/HTTP 或 OTLP/gRPC → Grafana Tempo
        │         │
        │         ▼
        │   Grafana Explore → Trace 视图 / Node Graph
        │
        └─► LangSmithTracker.mirror(...)              # 可选；任务类默认可关
```

映射约定（强制一致，便于多通道对照）：

| 本地字段 | OTLP / Tempo |
|----------|----------------|
| `request_id`（job 时=job_id） | Span 属性 `request_id`；检索主键（可与 `trace_id` 并存） |
| `kind` | 属性 `kind=request\|job` |
| 整次执行 | Root Span：`name={module}.request` 或 `{module}.job`，`service.name=models-app` |
| `nodes[]` 每一项 | Child Span：`name={node_id}`，`parent` = Root；时长 = `latency_ms`（按 `started_at`/`finished_at` 还原时间轴） |
| `status` / `error` | Span status + `error` 属性（截断） |
| `module` / `scene` / `degrade_reasons` | Span / Resource attributes |
| `meta.tempo_trace_id` | 导出成功后回写，便于从 `/ops/traces/{id}` 或 `/rag/traces/{job_id}` 跳转 |

**长任务导出策略（本期冻结）：**

1. **Store**：允许 `checkpoint` 多次覆盖，运维 HTTP 可看进行中阶段。
2. **OTLP**：默认 **仅 `finalize`（终态）导出一次**完整 Root+Children，用历史时间戳还原瀑布图；避免 RUNNING 期间重复 export 造成 Tempo 碎片。
3. 若后续需要「进行中也能在 Grafana 看到」：可增 `OTEL_JOB_LIVE_EXPORT=true`（非本期必达）。

**`trace_id` 策略（二选一，实现时定一种并写进代码注释）：**

1. **推荐**：落 Redis **前**预写 W3C `meta.tempo_trace_id`（`OTEL_PREASSIGN_TRACE_ID=true`），OTLP 导出复用同一 ID；Grafana Attribute 搜索业务 `request_id`（含 job_id）。
2. 备选：若 `request_id` 已是 UUID 且可规范为 128-bit，可派生 `trace_id`（需严格校验，避免非法 ID）。

### 8.3 应用侧实现要点

模块：`app/observability/otlp_exporter.py`（轻量 OTLP/HTTP JSON，失败 no-op）。

| 项 | 要求 |
|----|------|
| 输入 | 已脱敏的 `ExecutionTraceRecord`（先 `TraceSanitizer`） |
| 协议 | OTLP/HTTP JSON（`Content-Type: application/json`） |
| 开关 | `OTEL_TRACES_ENABLED` 或 `EXECUTION_TRACE_OTLP_ENABLED`；未部署 Tempo 时保持关 |
| 端点 | `OTEL_EXPORTER_OTLP_ENDPOINT` → **`http://monitoring-tempo:4318`**（同 `docker_vllm-network`）；跨网可用 `http://host.docker.internal:4318` |
| 采样 | `OTEL_TRACE_SAMPLE_RATE`（0~1）；与 LangSmith 采样独立 |
| 投递 | **异步**线程池；超时丢弃 + metrics |
| 失败 | 永不抛向业务/Job |

**推荐组合**：`EXECUTION_TRACE_BACKEND=redis` + Tempo profile；探活 `GET /ops/traces-status`。

配置项（建议写入 `.env.example`）：

| 变量 | 含义 | 建议默认 |
|------|------|----------|
| `EXECUTION_TRACE_OTLP_ENABLED` | 是否导出 OTLP | `false`（未部署 Tempo 时）；现场开 Tempo 后改 `true` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Tempo OTLP 入口 | `http://monitoring-tempo:4318` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | 兼容字段；实现为 `http/json` | `http/json` |
| `OTEL_SERVICE_NAME` | Resource `service.name` | `models-app` |
| `OTEL_TRACE_SAMPLE_RATE` | 采样率 | `1.0`（初期）/ 生产可降 |
| `OTEL_TRACE_MODULES` | 模块白名单 | 与 `EXECUTION_TRACE_MODULES` 对齐（含 `rag_ingest` 等） |
| `OTEL_JOB_LIVE_EXPORT` | 任务 RUNNING 是否增量导出 | `false`（本期） |
| `OTEL_PREASSIGN_TRACE_ID` | 落 Redis 前预写 `tempo_trace_id` | `true` |

### 8.4 monitoring-deploy 本期改造

在现有 `monitoring-deploy/`（Prometheus + Grafana + Alertmanager 等）上 **增补**：

| 交付物 | 说明 |
|--------|------|
| Tempo 服务 | `docker-compose` 增加 `tempo`（可用 `profiles: [tracing]` 可选启动，避免无 Trace 需求的现场被强制拉起） |
| 端口 | OTLP HTTP `4318`、OTLP gRPC `4317`（按镜像文档）；Grafana 仅连 Tempo 查询 API |
| Grafana 数据源 | provisioning 增加 **Tempo**；与 Prometheus 并列 |
| 网络 | Tempo 与 `models-app`、Grafana 同 Docker 网络或可路由；专网不出公网 |
| 保留策略 | Tempo 本地磁盘 + 短保留（如 24h~72h）；**长任务注意保留 ≥ 典型 Job 时长**；长期归档仍靠 Redis/ES Store |
| 文档 | `monitoring-deploy/README.md` 增补「开启 tracing profile / 用 request_id 或 job_id 查链路」步骤 |

**Grafana 使用方式（运维手册级，本期写清即可）：**

1. Explore → 数据源 Tempo。
2. 按属性搜索：`{resource.service.name="models-app" && span.request_id="<id>"}`（任务场景填 `job_id`；也可用 `span.module="rag_ingest"`）。
3. 打开 Trace：看 **瀑布图**（耗时）与 **Node Graph**（执行 DAG）。
4. Dashboard（可选本期）：变量 `$request_id` + Trace panel；Prom 面板「摄入失败突增」作为入口，复制 `job_id` 下钻。

### 8.5 与其它通道的分工（本期冻结）

| 通道 | 用途 |
|------|------|
| **Grafana + Tempo + Prom** | 现场运维、专网、告警联动、**请求级 / 任务级 DAG 可视化** |
| **本地 `/ops/traces*`** | 无 Tempo / 导出关闭时的保底回查；自动化与脚本；进行中 Job 的 checkpoint |
| **现有 Job API** | 取消、重试、文档/分块业务结果 |
| **LangSmith** | 研发 Prompt/效果实验（可选外网；任务类默认可关） |
| **文档 Mermaid** | 静态编排「本来长什么样」 |

Store / Tempo / LangSmith 共享同一套 `request_id`（含 job_id）与 `node_id`（阶段名）命名，避免口径分裂。

### 8.6 本期验收（本节专项）

| 场景 | 期望 |
|------|------|
| 开启 OTLP + Tempo（请求） | chatbot/analysis 结束后，Grafana 能按 `request_id` 打开 Trace，可见 Root + 子 Span |
| 开启 OTLP + Tempo（任务） | RAG 摄入 Job 终态后，按 `job_id` 可见 `validate/parse/chunk/embed/index` 等阶段 Span |
| Node Graph | 父子关系正确；耗时与本地 `/ops/traces/{id}` 或 `/rag/traces/{job_id}` 量级一致 |
| 关闭 OTLP / 未部署 Tempo | 业务与 Job 正常；本地 Store / Job API 仍可用 |
| 脱敏 | Tempo 中无完整密钥/文档正文/超大报告 |
| 采样=0 | 无导出、无错误刷屏 |

### 8.7 明确不作为本期交付

- 用 Grafana 绘制/维护静态 LangGraph 拓扑图
- 以 Infinity 调 `/ops/traces` 拼 Node Graph 作为主方案（可文档注明为无 Tempo 应急手段）
- Tempo 长期海量存储替代 Redis/ES 业务 Trace 归档
- Job 进行中实时 Tempo 增量刷新（`OTEL_JOB_LIVE_EXPORT`，后续可选）

## 9. 指标与告警（与 Prometheus 协同）

在 `app/core/metrics.py` 增补（示意）：

| 指标 | 类型 | 标签 |
|------|------|------|
| `execution_trace_saved_total` | Counter | `module`, `kind`, `status` |
| `execution_trace_save_errors_total` | Counter | `module`, `backend` |
| `execution_trace_checkpoint_total` | Counter | `module` |
| `otlp_export_total` | Counter | `module`, `result=ok\|error\|skipped` |
| `otlp_export_latency_seconds` | Histogram | `module` |
| `langsmith_runs_total` | Counter | `module`, `result=ok\|error\|skipped` |
| `langsmith_export_latency_seconds` | Histogram | `module` |

告警思路（可扩 `configs/monitoring/`）：

- Trace 保存错误率持续升高 → 检查 Redis/ES
- OTLP 导出错误率持续升高 → 检查 Tempo / 网络 / endpoint
- 某 module 降级原因突增 → 已有 analysis 规则可泛化到统一 degrade 标签

Grafana：**Prom 面板看趋势**；**Tempo 面板/Explore 看单次 DAG**；列表类 degrade Top 仍可走 HTTP Trace API 或 Prom 计数。

---

## 10. 实施分期

### Phase 0 — 设计落地（0.5~1 周）

- [ ] 合入本方案；评审模型字段、脱敏规则、OTLP 属性字典
- [ ] `.env.example` 预留 `EXECUTION_TRACE_*` / `EXECUTION_TRACE_OTLP_*` / `OTEL_*` / LangSmith 扩展项
- [ ] 明确鉴权：运维 traces 仅内网或 admin；Tempo/Grafana 不暴露公网

### Phase 1 — 骨架（1~2 周）

- [ ] `ExecutionTraceRecord` + `TraceSanitizer` + `ExecutionTraceStore`（memory/redis，ES 可随后）
- [ ] `TraceRecorder` + `GET /ops/traces/{id}` + list
- [ ] analysis：**双写投影**；analysis_agent：**切统一 Store**
- [ ] 单测：store CRUD、脱敏、API 404/200

### Phase 2 — 在线编排 + 任务类全覆盖（2~3 周）

- [ ] chatbot / nl2sql / llm_infer 埋点 + 模块别名 API
- [ ] **任务类**：`rag_ingest` / `inspection_extract` / `graph_rebuild` 阶段埋点 + `checkpoint` + `/rag/traces/{job_id}` 等别名
- [ ] 同步/SSE 响应统一带 `request_id`；异步提交统一可关联 `job_id`
- [ ] stats / trend / degrade-topn 支持 `kind=job` 过滤
- [ ] 文档：运维手册「如何用 request_id / job_id 查链路（HTTP + Job API 分工）」

### Phase 3 — 请求级 / 任务级链路可视化（本期，1~2 周；可与 Phase 2 后半并行）**【本期必达】**

- [ ] `OtlpTraceExporter`：Record → Root/Child Spans（含 `kind=job`）；异步、采样、脱敏；**终态导出**
- [ ] `monitoring-deploy`：Tempo 服务（建议 `profiles: [tracing]`）+ Grafana Tempo 数据源 provisioning
- [ ] `finalize` 回写 `meta.tempo_trace_id`（若可得）
- [ ] README：Explore 按 `request_id` / `job_id` 打开 Trace / Node Graph 的操作步骤
- [ ] 验收：§8.6 清单打勾（含 RAG 摄入 Job）

### Phase 4 — LangSmith 增强（1~2 周，可与 Phase 3 并行）

- [ ] Tracker 单例、父子 run、`mirror_execution_trace`
- [ ] analysis / analysis_agent 接入镜像；任务类按白名单可选
- [ ] 异步投递 + 采样率 + `langsmith_run_id` 回写
- [ ] 联调：有外网环境验证 UI；无外网验证 no-op

### Phase 5 — 收敛与清理（1 周）

- [ ] `/analysis/traces*` 切读统一 Store（适配器）
- [ ] 评估废弃重复投影；更新 `工程完成度总览` / 架构文档过时表述
- [ ] Prom↔Tempo 看板钻取打磨；评估 `OTEL_JOB_LIVE_EXPORT` 是否立项

---

## 11. 测试与验收

### 11.1 功能验收

| 场景 | 期望 |
|------|------|
| analysis 跑完 | `/ops/traces/{id}` 与 `/analysis/traces/{id}` 均可查到节点耗时 |
| analysis_agent | 重启单 worker 后 Redis 后端仍可查；多 worker 一致 |
| chatbot 流式结束 | `finished.meta.request_id` 可查到 intent/retrieve/answer 等节点 |
| **RAG 摄入 Job** | 终态后 `/rag/traces/{job_id}` 可见阶段节点；运行中 checkpoint 可查 `status=running`；`/rag/jobs/{job_id}` 仍可用 |
| **检修提取 Job** | 同上；取消 → Trace `aborted` |
| nl2sql | 校验失败仍有 Trace，`status=failed` 或 degrade 有因 |
| **OTLP + Tempo 开启** | Grafana 按 `request_id`/`job_id` 可见瀑布图与 Node Graph；与本地 nodes 一致 |
| **OTLP 关闭** | 业务与 Job 零报错；本地 API 仍可用 |
| LangSmith 关闭 | 业务零报错；无外呼 |
| LangSmith 开启 | UI 可见父/子 run，metadata 含同一 `request_id` |
| 超大 query | Store / Tempo / LangSmith 中原文被截断；无密钥明文 |

### 11.2 性能验收

- Trace `save` / `checkpoint` P99 对本请求或 Job 线程附加延迟可控（Redis 本机目标 &lt; 5ms，或异步落库不挡响应尾包）
- Job 阶段 checkpoint 不显著拖慢摄入吞吐
- OTLP / LangSmith 异步开启时，对 QPS / Job 吞吐无明显回压（用对应 `*_export_latency` 观察）

### 11.3 回归范围

- `tests/test_chatbot_*`、`tests/test_nl2sql_*`、`tests/test_*analysis*trace*`（新建统一 store / OTLP exporter mock 测试）
- 现有 analysis traces API 契约测试保持绿

---

## 12. 风险与对策

| 风险 | 对策 |
|------|------|
| Redis 容量被 Trace 打满 | TTL + `MAX_ITEMS` + 禁止存全文报告/文档正文；任务类尤甚 |
| Tempo 磁盘膨胀 | 短保留 + 采样；大字段不进 Span；长任务注意保留窗口 |
| 任务 checkpoint 写放大 | 按阶段 checkpoint，勿每 chunk 一次；多文档聚合 attributes |
| 双写两套 store 不一致 | 以 `request_id` 为准；投影失败打 metrics；后期单后端 |
| Store 与 Tempo 节点不一致 | 同源 `finalize` 一次映射；单测比对 latency |
| LangSmith 字段与 SDK 版本漂移 | 锁定 `langsmith` 版本；集成测试 mock Client |
| 隐私合规 | 强制 Sanitizer；运维 API / Grafana 鉴权；采样；禁止文档原文进 Trace |
| 埋点遗漏节点/阶段 | 代码评审清单 + 单测断言「必选节点/阶段集合」 |
| 与 Job API 概念混淆 | **Trace=阶段时间线**；**Job API=状态机与业务产物**；文档与 OpenAPI 互链 |
| 现场未开 tracing profile | 默认不强制 Tempo；文档标明开启步骤；HTTP Store + Job API 保底 |

---

## 13. 推荐落地顺序（执行清单摘要）

1. **统一模型（含 `kind=request|job`）+ Redis Store + `/ops/traces/{id}`**
2. **analysis 投影 + analysis_agent 迁出内存**
3. **chatbot / nl2sql / llm_infer 埋点与别名 API**
4. **本期任务类：rag_ingest / inspection_extract / graph_rebuild + Job 别名 traces API**
5. **stats/trend/degrade 对齐（含 kind 过滤）**
6. **本期：OTLP 导出（含 job 终态）+ monitoring-deploy Tempo + Grafana Node Graph / Trace 验收**
7. **LangSmith mirror（父子 run + 异步 + 回写 id；任务类可选）**
8. **文档与 `.env.example`、监控指标、告警样例**
9. **废弃重复实现，收敛到单一 Store**

---

## 14. 文档维护

| 变更后应同步 | 内容 |
|--------------|------|
| `app/app-deploy/.env.example` | `EXECUTION_TRACE_*`、`OTEL_*` / OTLP、LangSmith 扩展 |
| `monitoring-deploy/README.md` | Tempo profile、数据源、按 `request_id` 查链路 |
| `monitoring-deploy/docker-compose.yml` 等 | Tempo 服务与 Grafana provisioning |
| `docs/大小模型应用技术架构与实现方案.md` | §观测：本地 Trace（含任务类）+ Tempo/Grafana + LangSmith |
| `docs/工程完成度总览.md` | 修正「Analysis 已挂 LangSmith」等过时表述 |
| `README-DEPLOY-ASCEND.md` / 简版部署 | 专网：开 Trace + 可选 Tempo；关 LangSmith |
| `docs/系统Prometheus资源监控实现方案.md` | 交叉引用：指标 vs 请求/任务级 Trace |
| RAG / 检修提取 OpenAPI 说明 | Job API 与 `/traces/{job_id}` 互链 |

本方案批准后，实现 PR 建议按 Phase 拆分，避免单 PR 同时改全模块埋点、存储迁移与 Tempo 部署。

---

## 15. 实现状态（代码已落地）

| Phase | 状态 | 落点摘要 |
|-------|------|----------|
| 0 | **已实现** | `app/models/execution_trace.py`、`app/observability/*`、`.env.example` 变量 |
| 1 | **已实现** | `execution_trace_store`、`TraceRecorder`、`GET /ops/traces*`（含时间窗、`/result`） |
| 2 | **已实现** | analysis 投影、analysis_agent 兼容读、chatbot/nl2sql/llm/rag/inspection(+v0)/graph 旁路埋点 + 别名 |
| 3 | **已实现** | `OtlpTraceExporter` + Tempo profile + Grafana DS；**`tempo_trace_id` 回写 Store** |
| 4 | **已实现** | LangSmith 单例 + mirror；**`langsmith_run_id` 回写 Store** |
| 5 | **已收敛** | analysis `get_trace` 统一 Store 回退适配；失败路径/checkpoint；部署说明 |

**原则复核**：埋点均为 try/except 旁路；不改 Job 状态机与 LangGraph 边条件；默认 OTLP/LangSmith 关闭。

**推荐生产路径（Redis + Tempo）**：

| 组件 | 作用 | 关键配置 |
|------|------|----------|
| Redis Store | `/ops/traces*` JSON、TTL、module/kind 二级索引 | `EXECUTION_TRACE_BACKEND=redis` + `REDIS_URL`；未设 BACKEND 时有 Redis 则自动选用 |
| Tempo | Grafana 瀑布图 | `monitoring-deploy` profile `tracing`；`OTEL_EXPORTER_OTLP_ENDPOINT=http://monitoring-tempo:4318` |
| 探活 | 运维确认 | `GET /ops/traces-status` |

**已知保留项（非阻塞）**：ES Trace backend 不做（避免与 RAG ES 争用）；Chatbot 节点耗时仍为终态投影（非图内实时计时）；Prom↔Tempo 钻取看板可后续打磨。
