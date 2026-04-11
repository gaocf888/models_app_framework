# 核心组件与关系（大小模型统一基座）

## 1. 组件总体划分

从职责维度，将组件划分为以下几个大类：

1. **接入层组件**
2. **大模型能力组件**
3. **小模型能力组件**
4. **RAG / 上下文 / 会话组件**
5. **NL2SQL 组件**
6. **基础设施与运维组件**

下面按类别进行拆解，并说明组件间的调用与依赖关系。

---

## 2. 接入层组件

- **`ApiGateway`（FastAPI 应用根）**
  - 职责：统一路由注册、鉴权中间件、Trace ID 注入、异常处理。
  - 依赖：
    - 各功能域的 `*Service`（大模型/小模型/RAG/NL2SQL）。
    - 日志组件、监控中间件。

- **路由模块**
  - `LLMInferenceRouter`
    - 接口：`POST /llm/infer` 等。
    - 调用 `LLMInferenceService`。
  - `ChatbotRouter`
    - 接口：`POST /chatbot/chat`（兼容保留）、`POST /chatbot/chat/stream`（SSE 主用）。
    - 调用 `ChatbotService`。
  - `AnalysisRouter`
    - 接口：`POST /analysis/run`。
    - 调用 `AnalysisService`（Agentic RAG）。
  - `NL2SQLRouter`
    - 接口：`POST /nl2sql/query`。
    - 调用 `NL2SQLService`。
  - `SmallModelRouter`
    - 接口：`POST /small-model/channel/start/stop/update`，`GET /small-model/channel/status`。
    - 调用 `ChannelManagerService`。

---

## 3. 大模型能力组件

### 3.1 推理与模型管理

- **`LLMConfigRegistry`**
  - 职责：管理可用的大模型配置（模型名、vLLM endpoint、max_tokens、温度等）。
  - 依赖：配置中心/数据库。

- **`LLMClient` 抽象**
  - 职责：为 LangChain 封装对 vLLM/云端 LLM 的统一调用接口。
  - 典型方法：
    - `generate(prompt, model_id, **kwargs)`
    - `stream_generate(prompt, model_id, **kwargs)`（流式）
  - 实现：
    - `VLLMClient`：对接自建 vLLM。
    - `CloudLLMClient`：对接云端模型。

- **`LLMInferenceService`**
  - 职责：提供通用大模型推理服务接口：
    - 支持 RAG/上下文/多模态输入配置；
    - 暴露给 API 层使用。
  - 依赖：
    - `LLMConfigRegistry`
    - `LLMClient`
    - `RAGService`（可选）
    - `ConversationManager`（可选）

### 3.2 LangChain / LangGraph 编排与 Agent

- **`ChainFactory`**
  - 职责：基于配置创建 LangChain 链/Agent：
    - Chatbot 链；
    - 综合分析 Agent；
    - NL2SQL 链等。
  - 依赖：
    - `LLMClient`
    - RAG 组件
    - 会话组件
    - Prompt 模板仓库

- **`PromptTemplateRegistry`**
  - 职责：管理所有 Prompt 模板（多版本）与 A/B 测试策略。
  - 功能：
    - 根据「场景 + 版本/策略」返回对应模板；
    - 支持灰度/随机/规则分流。

- **`LangSmithTracker`（可选）**
  - 职责：将链路执行信息上报到 LangSmith，用于调试与评估。
  - 依赖：LangSmith SDK。

- **`ChatbotLangGraphRunner`**
  - 职责：智能客服图编排（`fault_case_gate` 相似案例门控、意图分类、RAG 引擎路由、C-RAG、finalize；Runner 层流式、可选相似案例二次 RAG 与 SSE `meta`）。
  - 依赖：
    - `PromptTemplateRegistry`（模板与 A/B）
    - `ConversationManager`（历史读取与落库）
    - `HybridRAGService` / `AgenticRAGService`
    - `VLLMHttpClient`（底层流式调用）

---

## 4. 小模型能力组件

### 4.1 通道与线程模型

- **`ChannelManager`**
  - 职责：
    - 维护 `channel_id → ChannelContext` 的映射；
    - 管理 per-channel 锁、stop_event、解码/推理线程或 Future；
    - 对外提供 `start/stop/update/get_status` 等接口。
  - 线程安全：
    - 使用全局对象锁 `_objects_lock` 保护映射；
    - 每通道维护 `channel_lock` 保证该通道的操作与配置更新的互斥。

- **`ChannelContext`**
  - 内容：
    - `channel_id`
    - 视频源/配置
    - `message_queue`（有界队列）
    - `stop_event`
    - `channel_lock`
    - 解码线程句柄 / 推理线程句柄或 Future
    - 算法配置（模型名称、权重路径、预处理参数等）
    - 业务扩展字段（ROI、points、is_moving 等）

- **`DecoderWorker`**
  - 职责：
    - 从视频源拉流与解码；
    - 将解码后的帧放入 `message_queue`；
    - 根据 `stop_event` 与 `__stop__` 语义安全退出。

- **`InferenceWorker`**
  - 职责：
    - 从 `message_queue` 中取出帧；
    - 按通道算法配置执行小模型推理；
    - 触发时保存证据（帧图片/视频片段）并将结果回调到业务 Web 服务（可配置）。
  - 注意：
    - 对通道上下文的可变部分在 `channel_lock` 下读写或基于快照。

### 4.2 算法与训练组件

- **`SmallModelRegistry`**
  - 职责：维护所有小模型算法配置（YOLO/Seg/Cls 等）。

- **`SmallModelInferenceEngine`**
  - 职责：
    - 按 `algor_type` 选择策略执行推理（`app/small_models/strategy/*`）；
    - 合并配置优先级：API 参数 > 本地配置（`configs/small_model_algorithms.yaml`）；
    - 触发时保存证据并回调。

- **`SmallModelAlgorithmRegistry`**
  - 职责：加载本地算法类型配置 `configs/small_model_algorithms.yaml`，提供 `algor_type -> AlgorithmConfig` 映射（供引擎按类型选择策略、权重、阈值、回调等）。

- **`EvidenceStore` / `ClipRecorder`**
  - 职责：证据保存封装（帧图片/视频片段）。

- **`CallbackClient`**
  - 职责：将检测结果（含证据路径、检测框等）回调到业务 Web 服务。

- **`SmallModelTrainingService`**
  - 职责：管理小模型训练任务（代码方式）；
  - 集成 TensorBoard 可视化。

---

## 5. RAG / 上下文 / 会话组件

### 5.1 RAG 相关

- **`VectorStoreProvider`**
  - 职责：对接向量数据库（FAISS/Milvus/pgvector/ES）。
  - 能力：
    - 索引构建、更新、删除；
    - 多命名空间管理（schema/biz_knowledge/qa_examples 等）。

- **`EmbeddingService`**
  - 职责：统一封装嵌入模型调用（sentence-transformers）。加载策略为配置化的「离线优先、在线回退」：`EMBEDDING_MODEL_PATH` 指定本地目录时优先加载，否则按 `EMBEDDING_MODEL_NAME` 从 HuggingFace 下载；在线失败时记录日志并抛出异常。依赖见项目 `requirements-大模型应用.txt`。

- **`RAGService`**
  - 职责：
    - 传统 RAG：单轮检索 + 上下文拼接；
    - Agentic RAG：为 Agent 提供检索工具；
  - 依赖：
    - `VectorStoreProvider`
    - `EmbeddingService`

- **`GraphIngestionService`**
  - 职责：在 RAG 摄入阶段，从文本分片中抽取实体与关系并写入图数据库（当前基于 Neo4j + LangChain Graph 设计骨架）；是否启用由 `RAGConfig.graph.enabled` 控制。
  - 能力（规划中）：
    - 支持有/无领域本体（GraphSchemaConfig）两种模式：有本体时按 Schema 映射节点/关系类型；无本体时采用宽松的通用节点/关系表示。

- **`GraphQueryService`**
  - 职责：在检索阶段根据问题或候选实体，从图数据库中查询相关子图或事实，为 GraphRAG 或混合检索提供结构化上下文。

- **`HybridRAGService`**
  - 职责：封装“仅向量 / 仅图 / 向量 + 图混合”三种检索模式，对上层（Chatbot/分析/NL2SQL 等）暴露统一的 `retrieve` 接口。
  - 配置：读取 `RAGConfig.graph.strategy`（GraphHybridStrategyConfig），根据 `mode`、`vector_weight`、`graph_weight`、`max_context_items` 等控制行为。默认配置保持纯向量 RAG，与现有实现兼容。

- **`RAGIngestionService`**
  - 职责：
    - 管理 RAG 知识摄入流程，包括：
      - 文档、Schema、业务规则、问答样例、多模态特征等的解析与切分；
      - 调用 `EmbeddingService` 进行向量化；
      - 调用 `VectorStoreProvider` 写入/更新/删除向量；
    - 维护知识库命名空间与版本信息，支持不同业务域的隔离。
  - 对外接口（通过 API 路由）：
    - 注册/更新/删除知识源；
    - 触发 Schema 同步与重建索引；
    - 查询当前知识库状态（统计信息、更新时间等）。

### 5.2 会话与上下文

- **`ConversationStore`**
  - 职责：
    - 基于 Redis 存储用户会话（历史消息、NL2SQL 历史查询记录、摘要等）。
  - 能力：
    - `append_message`
    - `get_recent_history`
    - `get_or_update_summary`

- **`ConversationManager`**
  - 职责：
    - 为 Chatbot、综合分析、NL2SQL 提供统一的会话接口；
    - 封装上下文是否启用、长度裁剪策略。

---

## 6. NL2SQL 组件

基于《NL2SQL系统概要设计》进行封装：

- **`SchemaMetadataService`**
  - 职责：
    - 从业务数据库拉取并维护表结构、字段、注释、主外键关系等；
    - 与 RAG 知识库同步。

- **`NL2SQLRAGService`**
  - 职责：
    - 针对 Schema、业务规则、样例问答构建特定命名空间；
    - 提供问题 → 上下文的 RAG 检索能力。

- **`PromptBuilder`（NL2SQL 专用）**
  - 职责：
    - 按 NL2SQL 约定的 Prompt 结构（System/Schema/Biz Knowledge/Examples/User）生成最终 Prompt。

- **`NL2SQLChain` / `NL2SQLAgent`**
  - 职责：
    - 调用 LLM （通过 `LLMClient`）生成候选 SQL；
    - 控制一轮或多轮自我修正流程。

- **`SQLValidator`**
  - 职责：
    - 对生成 SQL 做语法与安全校验；
    - 控制只读、限制高危操作与资源消耗。

- **`SQLExecutor`**
  - 职责：
    - 多数据源适配与实际 SQL 执行；
    - 返回结果并进行格式化。

- **`NL2SQLService`**
  - 职责：
    - 将上述组件聚合为对外服务接口；
    - 与会话管理、RAG、监控集成；
    - 提供管理能力接口（如 Schema 刷新、测试查询、评估用例执行等）。

---

## 7. 监控、日志与运维组件

- **`LoggingManager`**
  - 职责：
    - 提供统一 `get_logger` 接口；
    - 全局日志级别与输出（控制台 + 文件）可配置；
    - 日志格式 JSON 化，兼容 Loki。

- **`MetricsCollector`**
  - 职责：
    - 基于 Prometheus Client 暴露 HTTP `/metrics`；
    - 注册各类 Counter/Gauge/Histogram。

- **`Tracing`（可选）**
  - 职责：
    - 对关键链路打 Trace（如 OpenTelemetry）。

---

## 8. 组件关系概览（文字版）

- API 层各 `*Router` 调用对应 `*Service`。
- 大模型相关：
  - `ChatbotService` / `AnalysisService` / `NL2SQLService` → `ChainFactory` / `NL2SQLChain` → `RAGService` + `ConversationManager` + `LLMClient` → vLLM/云端 LLM。
- 小模型相关：
  - `SmallModelRouter` → `ChannelManagerService` → `ChannelManager` → `DecoderWorker` + `InferenceWorker` → `SmallModelInferenceEngine`。
- RAG/会话：
  - 所有需要上下文/检索的服务通过 `RAGService` 与 `ConversationManager` 复用统一实现。
- NL2SQL：
  - `NL2SQLRouter` → `NL2SQLService` → `NL2SQLRAGService` + `PromptBuilder` + `NL2SQLChain` + `SQLValidator` + `SQLExecutor`。
- 运维：
  - 所有组件通过 `LoggingManager` 写日志；
  - 关键路径打点到 `MetricsCollector` 暴露给 Prometheus。

