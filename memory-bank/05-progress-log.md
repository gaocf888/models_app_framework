# 进度与变更日志（时间序列）

> 本文件用于细粒度记录与本技术基座相关的变更（按时间顺序），便于后续回溯。  
> 建议采用「YYYY-MM-DD HH:MM」+「作者/主体」+「简要说明」的形式。

---

## 2026-03-16 初始化

- 建立 `memory-bank/` 目录，用作整个大小模型技术基座的「记忆库」与单一事实源。
- 新增文件：
  - `00-project-overview.md`：梳理项目定位、目标与技术栈。
  - `01-architecture.md`：给出整体架构视图与关键技术决策（大小模型区分、LangChain 编排层、RAG/会话、监控等）。
  - `02-components.md`：列出核心组件清单及其依赖关系。
  - `03-development-process.md`：规范代码结构、开发与上线流程、并发安全约定。
  - `04-progress.md`：定义阶段性里程碑。
  - `05-progress-log.md`：当前文件，本次为首条记录。

## 2026-03-16 工程骨架 TODO-1 实现

- 按 `03-development-process.md` 中的设计，开始落地工程骨架：
  - 新增 `app/main.py`：FastAPI 应用入口，注册 `/health` 与 `/metrics`，并接入基础 Prometheus 指标中间件。
  - 新增 `app/core/logging.py`：统一日志初始化与 `get_logger` 封装。
  - 新增 `app/core/metrics.py`：HTTP 请求计数与时延指标的 Prometheus 定义。
  - 新增 `app/api/healthcheck.py`：健康检查路由，用于存活/就绪探针。

> 至此，TODO-1（工程骨架与目录结构落地的最小可用版本）已经具备可启动的基础服务能力。

## 2026-03-16 统一配置中心 TODO-2 实现

- 实现统一配置中心的初始版本：
  - 新增 `app/core/config.py`：
    - 定义 `AppConfig`/`LLMConfig`/`RAGConfig`/`LoggingConfig`/`PromptConfig` 等数据结构。
    - 提供 `get_app_config()` 单例访问入口，从环境变量加载最小化配置（支持默认 vLLM endpoint、日志级别/格式等）。
  - 更新 `app/core/logging.py`：
    - 从 `AppConfig.logging` 中读取日志级别与 JSON 格式开关，实现基础 JSON 日志能力，为后续 Loki 集成做准备。
  - 更新 `app/main.py`：
    - 在应用启动时先加载配置，再初始化日志，确保后续模块读取到一致的配置状态。

> 至此，TODO-2（统一配置中心与策略管理的骨架）已经完成基础能力，后续可以在此结构上扩展模型路由、RAG 策略与 Prompt A/B 配置。

## 2026-03-16 LLM 客户端与配置中心深化（TODO-2 深化版）

- 在原有配置中心基础上，继续完善大模型调用与配置管理骨架：
  - 新增 `app/llm/client.py`：
    - 定义 `LLMClient` 抽象与 `VLLMHttpClient` 实现，面向 vLLM 的 HTTP（OpenAI 兼容）调用；
    - 支持从 `LLMModelConfig` 中读取 endpoint/api_key/max_tokens/temperature 等参数。
  - 新增 `app/llm/config_registry.py`：
    - 封装对 `AppConfig.llm` 的访问，提供默认模型与模型列表查询能力。
  - 更新 `app/core/config.py`：
    - LLMConfig/LLMModelConfig 结构与上述客户端/注册中心协同工作，为后续接入 LangChain/Agent 提供基础。

## 2026-03-16 RAG 基础设施与会话管理 TODO-3 实现

- 初步实现 RAG 基础设施与会话管理骨架：
  - 新增 `app/rag/vector_store.py`：
    - 定义 `VectorStore` 抽象接口与 `InMemoryVectorStore` 内存实现（支持嵌入存储与余弦相似度检索）。
    - 定义 `VectorStoreProvider`，根据 `AppConfig.rag.vector_store_type` 选择实现（当前为内存版占位，后续扩展 FAISS/Milvus/pgvector 等）。
  - 新增 `app/rag/embedding_service.py`：
    - 定义 `EmbeddingService` 使用简单 bag-of-characters 特征生成固定长度向量，为接入真实嵌入模型预留接口。
  - 新增 `app/rag/rag_service.py`：
    - 定义 `RAGService`，从 `AppConfig.rag` 读取 top_k 等参数，基于 EmbeddingService + VectorStore 提供余弦相似度检索，并支持文本批量索引。
  - 新增 `app/conversation/store.py`：
    - 定义内存版 `ConversationStore`，提供 `append_message` / `get_recent_history` / `clear` 等基础能力。
  - 新增 `app/conversation/manager.py`：
    - 定义 `ConversationManager`，封装 user/assistant 消息追加与历史查询接口，面向 Chatbot/分析/NL2SQL 等上层使用。

> 至此，TODO-3（RAG 基础设施与 Conversation 管理的最小骨架）已经完成，后续可以在此基础上替换为 Redis 与真实向量库实现，并接入 LangChain 链路。

## 2026-03-16 智能客服链路 V1 TODO-4 实现

- 实现智能客服基础链路（V1，占位版）：
  - 新增 `app/models/chatbot.py`：
    - 定义 `ChatRequest` / `ChatResponse` / `ChatMessage` 等 Pydantic 数据模型，支持 RAG/上下文开关。
  - 新增 `app/services/chatbot_service.py`：
    - 定义 `ChatbotService`，集成 `RAGService` 与 `ConversationManager`：
      - 按需检索上下文片段（enable_rag）。
      - 记录并读取会话历史（enable_context）。
      - 目前生成占位回答，为后续接入 LangChain + LLMClient 预留接口。
  - 新增 `app/api/chatbot.py`：
    - 暴露 `POST /chatbot/chat` 接口（基础 JSON 响应版）。
    - 后续将扩展为流式响应（SSE/WebSocket）与更丰富的入参/出参。
  - 更新 `app/main.py`：
    - 注册 `chatbot` 路由到 `/chatbot` 前缀。

> 至此，TODO-4（智能客服链路 V1）已完成骨架实现，具备最小可用对话能力，并与 RAG/会话管理打通，为下一步接入 LangChain 和真实大模型做准备。

## 2026-03-16 综合分析 Agent 架构 V1 TODO-5 实现

- 实现综合分析 Agent 链路的基础骨架（V1，占位版）：
  - 新增 `app/models/analysis.py`：
    - 定义 `AnalysisInput` / `AnalysisResult`，支持文本描述与多模态数据引用 ID（图像/视频/GPS/传感器等）。
  - 新增 `app/services/analysis_service.py`：
    - 定义 `AnalysisService`，集成 `RAGService` 与 `ConversationManager`：
      - 可选基于分析查询启用 RAG 检索；
      - 记录分析请求与结果摘要到会话上下文。
    - 目前返回占位分析报告，后续将接入 Agentic RAG + 大模型 + 工具调用。
  - 新增 `app/api/analysis.py`：
    - 暴露 `POST /analysis/run` 接口，完成综合分析基础链路的打通。
  - 更新 `app/main.py`：
    - 注册 `analysis` 路由到 `/analysis` 前缀。

> 至此，TODO-5（综合分析 Agent 架构 V1）已完成骨架实现，为后续引入 LangChain/LangGraph、工具调用与多模态数据处理奠定基础。

## 2026-03-16 小模型通道管理与推理流水线 V1 TODO-6 实现

- 实现小模型通道管理与推理流水线的基础骨架（V1，占位版）：
  - 新增 `app/small_models/channel_manager.py`：
    - 定义 `ChannelConfig`/`ChannelContext` 与 `ChannelManager`，实现通道的 start/stop/update/status；
    - 使用全局对象锁 `_objects_lock` 管理 `channel_id -> ChannelContext` 映射，每通道一把 `channel_lock`。
  - 新增 `app/small_models/workers.py`：
    - 定义解码线程与推理线程的占位循环（向队列写入/读取“伪帧”），用于打通通道结构。
  - 新增 `app/small_models/inference_engine.py`：
    - 定义 `SmallModelInferenceEngine` 占位实现，后续将加载实际 YOLO/Seg/Cls 等模型。
  - 新增 `app/models/small_model.py`：
    - 定义 `SmallModelChannelConfig` / `SmallModelChannelStatus` Pydantic 模型。
  - 新增 `app/services/small_model_channel_service.py`：
    - 封装 ChannelManager，对外以 Pydantic 模型形式提供通道管理接口。
  - 新增 `app/api/small_model.py`：
    - 暴露 `/small-model/channel/start|stop|update|status` 等管理接口。
  - 更新 `app/main.py`：
    - 注册 `small_model` 路由到 `/small-model` 前缀。

> 至此，TODO-6（小模型通道管理与推理流水线 V1）已完成骨架实现，符合《高并发多线程安全应用架构设计》中“通道 + 队列 + 推理”方案的基本结构要求，后续可在此基础上集成真实视频流与小模型推理逻辑。

## 2026-03-16 小模型多算法配置与训练封装 TODO-9 实现

- 初步实现小模型多算法配置与训练封装骨架：
  - 新增 `app/small_models/registry.py`：
    - 定义 `SmallModelMeta` 与 `SmallModelRegistry`，用于注册与查询可用小模型及其元数据（名称、描述、权重路径等）。
  - 新增 `app/small_models/training.py`：
    - 定义 `SmallModelTrainingConfig` 与 `SmallModelTrainingService`：
      - 支持以 `job_id + config` 形式启动训练任务（后台线程占位实现）。
      - 后续将接入真实训练逻辑与 TensorBoard 日志写入。

> 至此，TODO-9（小模型多算法配置与训练封装骨架）已完成，为后续接入实际 YOLO/Seg/Cls 模型与 TensorBoard 监控提供了结构基础。

---

> 后续所有与架构、组件、流程相关的重要变更，请在这里追加记录，并同步更新相关文档。

## 2026-03-16 NL2SQL 子系统集成 V1 TODO-8 实现

- 实现 NL2SQL 子系统的基础骨架（V1，占位版）：
  - 新增 `app/nl2sql/schema_service.py`：
    - 定义 `SchemaMetadataService`，使用内存结构保存表/字段元数据，为后续从真实数据库同步 Schema 做准备。
  - 新增 `app/nl2sql/rag_service.py`：
    - 定义 `NL2SQLRAGService`，复用通用 `RAGService`，为 Schema/业务知识等提供检索封装。
  - 新增 `app/nl2sql/prompt_builder.py`：
    - 定义 `PromptBuilder`，根据问题与 Schema 片段构建 NL2SQL 提示词。
  - 新增 `app/nl2sql/validator.py`：
    - 定义 `SQLValidator`，限制生成 SQL 为只读 SELECT，并粗略拦截高危语句。
  - 新增 `app/nl2sql/executor.py`：
    - 定义 `SQLExecutor` 骨架，后续将接入真实数据库执行与多数据源适配。
  - 新增 `app/nl2sql/chain.py`：
    - 定义 `NL2SQLChain`，通过 RAG 检索 Schema 片段 + PromptBuilder 构建提示词 + `VLLMHttpClient` 调用大模型生成 SQL，并用 SQLValidator 做基础校验。
  - 新增 `app/models/nl2sql.py`：
    - 定义 `NL2SQLQueryRequest` / `NL2SQLQueryResponse` Pydantic 模型。
  - 新增 `app/services/nl2sql_service.py`：
    - 定义 `NL2SQLService`，封装 NL2SQLChain + SQLExecutor + ConversationManager。
  - 新增 `app/api/nl2sql.py`：
    - 暴露 `POST /nl2sql/query` 接口，完成 NL2SQL 基础链路的打通。
  - 更新 `app/main.py`：
    - 注册 `nl2sql` 路由到 `/nl2sql` 前缀。

> 至此，TODO-8（NL2SQL 子系统集成 V1）已完成，可以在后续 TODO 中接入真实数据库、Schema 同步流程与评估闭环。

## 2026-03-16 统一 Prompt 管理与 A/B 测试机制 TODO-12 实现

- 实现提示词模板与 A/B 测试机制的基础能力：
  - 新增 `configs/prompts.yaml`：
    - 为 `chatbot` / `analysis` / `nl2sql` 各定义多个版本的 Prompt 模板（version/weight/description/content）。
    - 通过 weight 字段为后续 A/B 实验提供权重配置。
  - 新增 `app/llm/prompt_registry.py`：
    - 定义 `PromptTemplate` 数据结构和 `PromptTemplateRegistry`：
      - 从 `configs/prompts.yaml` 加载多场景、多版本模板。
      - 提供 `get_template(scene, user_id, version)` 接口：
        - 支持按 version 精确获取指定版本；
        - 在未指定 version 时，基于 user_id 的哈希和权重进行 A/B 分流，选择模板版本。

> 至此，TODO-12（统一 Prompt 管理与 A/B 测试机制）已完成骨架实现，后续可以在各链路（Chatbot/Analysis/NL2SQL）中按场景调用 PromptTemplateRegistry，并结合 LangSmith 做效果评估。

## 2026-03-16 大模型训练/微调管理 TODO-10 实现

- 实现大模型训练/微调管理骨架：
  - 新增 `app/train/llm_factory_adapter.py`：
    - 定义 `LLaMAFactoryConfig` 与 `LLaMAFactoryAdapter`，用于封装与 LLaMA-Factory 的交互入口（可视化训练/微调通道）。
    - 当前实现仅记录启动参数，为后续按实际部署方式（本地脚本/远程服务）接入留出接口。
  - 新增 `app/train/llm_training.py`：
    - 定义 `LLMTrainingConfig` 与 `LLMTrainingService`，用于封装代码方式的大模型训练/微调任务；
    - 支持配置 base_model/dataset_path/output_dir/mode(lora/full)/resume_from_checkpoint 等信息。

> 至此，TODO-10（大模型训练/微调管理骨架）已完成，为后续接入真实训练脚本和 LLaMA-Factory 服务提供了统一的适配与配置入口。

## 2026-03-16 日志与监控指标扩展 TODO-13（部分）实现

- 在现有 LoggingManager 和 /metrics 基础上，补充了各子系统的关键指标：
  - `app/core/metrics.py`：
    - 新增 LLM 指标：`llm_requests_total`、`llm_request_latency_seconds`；
    - 新增 RAG 指标：`rag_queries_total`；
    - 新增小模型指标：`small_model_frames_processed_total`；
    - 新增 NL2SQL 指标：`nl2sql_queries_total`、`nl2sql_query_errors_total`。
  - 在关键路径中植入指标打点：
    - `app/llm/client.py`：在 `VLLMHttpClient.generate` 中记录 LLM 调用次数与耗时；
    - `app/rag/rag_service.py`：在 `retrieve_context` 中记录 RAG 查询次数；
    - `app/small_models/workers.py`：在推理循环中记录已处理帧数量；
    - `app/services/nl2sql_service.py`：记录 NL2SQL 查询次数与执行错误次数。

> 至此，TODO-13 中“应用级日志与监控”部分已具备最小集指标，后续可以在 Prometheus/Grafana/Loki 层面完成仪表盘与告警配置。

## 2026-03-16 Redis 会话存储与 RAG 摄入 + Schema 同步（下一阶段工作清单 TODO-P4/P6/P10）实现

- **会话存储（TODO-P4）**
  - 更新 `app/conversation/store.py`：
    - 保留内存版 `ConversationStore`，新增 `RedisConversationStore` 与 `get_default_store` 工厂方法；
    - 当设置环境变量 `REDIS_URL` 且安装了 `redis[asyncio]` 时，默认使用 Redis 存储会话，实现多进程/多实例共享。
  - 更新 `app/conversation/manager.py`：
    - 默认使用 `get_default_store()`，自动根据环境选择 Redis 或内存实现。

- **RAG 摄入服务与管理 API（TODO-P6）**
  - 新增 `app/rag/ingestion.py`：
    - 定义 `RAGDatasetMeta` 与 `RAGIngestionService`，封装文本知识摄入与数据集元信息管理；
    - 与 `EmbeddingService`、`VectorStoreProvider` 协同，将文本转换为向量并写入向量库。
  - 新增 `app/api/rag_admin.py`：
    - 提供 `/rag/ingest/texts` 接口，用于将一批文本摄入指定数据集；
    - 提供 `/rag/datasets` 接口，用于查看当前登记的 RAG 数据集列表。
  - 更新 `app/main.py`：
    - 注册 `rag_admin` 路由到 `/rag` 前缀。

- **NL2SQL Schema 同步（TODO-P10）**
  - 更新 `app/nl2sql/schema_service.py`：
    - 使用 SQLAlchemy AsyncEngine 基于 `DatabaseConfig.url` 连接真实数据库；
    - 新增 `refresh_from_db()` 异步方法，通过元数据反射加载所有表结构（表名与列名/类型），并更新内存中的 Schema 映射；
    - 保留示例 `orders` 表 Schema，便于在无数据库时本地调试。

> 至此，《下一阶段工作清单.md》中 TODO-P4/P6/P10 对应的代码层实现已完成，后续可在此基础上接入 Redis 实例、完善 RAG 摄入类型（Schema/业务知识等）并通过 API 触发 NL2SQL Schema 刷新。

## 2026-03-16 Chatbot LangChain 编排接入（下一阶段工作清单 TODO-P1/P2 部分）实现

- 新增 `app/llm/chains/chatbot_chain.py`：
  - 使用 LangChain（langchain-openai）构建 `ChatbotChain`：
    - 调用 vLLM 的 OpenAI 兼容接口；
    - 集成 `PromptTemplateRegistry` 选择 chatbot 场景 Prompt（支持 A/B 分流）；
    - 集成 `RAGService` 与 `ConversationManager`，可按开关拼接历史上下文与 RAG 检索结果。
  - 模块顶部有详细注释，说明依赖与使用方式（依赖安装见 `docs/下一阶段工作清单-未完成说明-实现说明.md`）。
- 更新 `app/services/chatbot_service.py`：
  - 构造函数中尝试初始化 `ChatbotChain`：
    - 若依赖可用，则启用 LangChain 链路作为主实现；
    - 若依赖缺失，则记录 warning 并回退到原有占位实现。
  - `chat` 方法优先通过 `ChatbotChain.run(...)` 完成对话，保证在启用 LangChain 时真正使用大模型生成回答。

> 至此，Chatbot 场景已具备企业级的 LangChain 编排骨架，后续可按同样模式将 Analysis 和 NL2SQL 链路 LangChain 化。

## 2026-03-17 通用 LLM 推理服务与 /llm/infer 接口（下一阶段工作清单 TODO-P1）实现 + 多步 Agent Workflow 骨架

- 新增通用大模型推理能力并打通统一接口：
  - 新增 `app/models/llm.py`：
    - 定义 `ChatMessage` / `LLMInferenceRequest` / `LLMInferenceResponse`，支持单轮 `prompt` 与多轮 `messages` 两种调用模式；
    - 支持配置模型 ID、Prompt 版本，以及 RAG/上下文开关等参数；
    - 追加可选字段 `rag_mode`（`basic` / `agentic`），当前默认行为等价于 `basic`，作为在通用推理层接入 Agentic RAG 能力的扩展开关。
  - 新增并扩展 `app/services/llm_inference_service.py`：
    - 实现 `LLMInferenceService`，统一处理 LLM 推理请求：
      - 集成 `PromptTemplateRegistry`（scene=`llm_inference`）选择 Prompt 模板；
      - 集成 `RAGService` 与 `ConversationManager`，在启用开关时自动拼接上下文；
      - 接入 `AgenticRAGService` 基座，在启用 RAG 时通过 `rag_mode`（默认 basic，可选 agentic）控制底层 RAG 模式；
      - 在 `rag_mode="agentic"` 且 LangChain 可用时，增加 `_analyze_intent_and_plan(...)` 预处理步骤，对当前问题进行轻量诊断与子任务规划，并将规划摘要用于增强 RAG 检索 query 与系统提示；
      - 优先使用 LangChain 的 `ChatOpenAI` 作为编排层，未安装相关依赖时回退到内部 `VLLMHttpClient`。
  - 新增 `app/api/llm_inference.py`：
    - 定义 `POST /llm/infer` 接口，接收 `LLMInferenceRequest` 并返回 `LLMInferenceResponse`。
  - 更新 `app/main.py`：
    - 注册新的 `llm_inference` 路由前缀 `/llm`。

> 至此，下一阶段工作清单中的 TODO-P1「统一 LLM 推理服务 + LangChain 接入」在代码层已完成首版落地，并在此基础上增加了与蓝图一致的“问题诊断 + Agentic RAG”多步骨架，可作为 Chatbot/Analysis/NL2SQL 等场景的基础推理能力复用。

## 2026-03-17 LangSmith 可选 tracing 中间层（下一阶段工作清单 TODO-P3）实现

- 新增 `app/llm/langsmith_tracker.py`：
  - 定义 `LangSmithTracker` 与 `LangSmithSettings`，通过环境变量 `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `LANGSMITH_ENABLED` 控制启用；
  - 在未配置或初始化失败时自动降级为 no-op，避免对主链路产生影响。
- 在以下链路中接入基础 trace 上报：
  - Chatbot：在 `app/llm/chains/chatbot_chain.py` 的 `ChatbotChain.run` 中记录输入、输出与场景元数据；
  - 通用 LLM 推理：在 `app/services/llm_inference_service.py` 的 `LLMInferenceService.infer` 中记录模型、Prompt 版本、RAG/上下文开关等信息；
  - 综合分析：在 `app/llm/chains/analysis_chain.py` 的 `AnalysisChain.run` 中记录分析请求与生成的摘要；
  - NL2SQL：在 `app/nl2sql/chain.py` 的 `NL2SQLChain.generate_sql` 中记录自然语言问题与生成的 SQL。

> 至此，TODO-P3 对 LangSmith 可选集成中间层的“代码侧落地”已完成，后续可在实际接入 LangSmith 服务时补充更丰富的指标字段并与 LangChain 内置 tracing 方式统一配置。

## 2026-03-17 小模型训练流程与 TensorBoard + 训练任务管理骨架（下一阶段工作清单 TODO-P7/P8）实现

- 更新 `app/small_models/training.py`：
  - 扩展 `SmallModelTrainingConfig`，支持 `batch_size`、`learning_rate`、`log_dir`、`output_dir` 等训练参数；
  - 新增 `SmallModelTrainingJob` 数据结构，记录训练任务的 `job_id/config/status/created_at/started_at/finished_at/log_dir/output_dir/error` 等元数据；
  - 在 `SmallModelTrainingService` 中：
    - 使用 `SmallModelRegistry` 解析小模型的默认权重路径，以便推导默认 `output_dir`；
    - 维护进程内的训练任务字典与后台线程映射（支持 `start_training/get_job/list_jobs` 等方法）；
    - 在 `_run_job` 中：
      - 延迟导入 PyTorch 与 TensorBoard 依赖，未安装时优雅失败并记录错误；
      - 使用占位 `DummyDataset` 和 `DummyModel` 构建最小可运行训练循环，按 epoch 计算 loss；
      - 使用 `SummaryWriter` 将训练 loss 写入 `runs/small_models/{job_id}` 等日志目录；
      - 将示例模型权重保存到推导出的 `output_dir/best.pt`。

> 至此，TODO-P7/P8 对“小模型训练流程 + TensorBoard + 训练任务管理”的代码骨架已落地，具备最小可运行训练与指标记录能力，后续可在此基础上接入真实小模型算法与对外训练管理 API。

## 2026-03-17 大模型训练/微调管理闭环骨架（下一阶段工作清单 TODO-P9）实现

- 新增统一训练调度器与管理 API：
  - 新增 `app/train/orchestrator.py`：
    - 定义 `LLMTrainingJob` 元数据结构，统一记录 `job_id/mode(base_model/dataset_path/output_dir)/status/created_at/started_at/finished_at/error` 等；
    - 定义 `TrainingOrchestrator`，统一封装 factory/code 两种训练模式的任务启动与状态管理：
      - `start_llm_training(job_id, mode, factory_cfg, code_cfg)`：根据 mode 调用 `LLaMAFactoryAdapter` 或 `LLMTrainingService` 启动后台线程；
      - `get_job` / `list_jobs`：提供基础任务查询能力。
  - 新增 `app/models/train.py`：
    - 定义 `LLMTrainJobRequest` 与 `LLMTrainJobStatus` Pydantic 模型，作为 `train_admin` 接口的入参/出参。
  - 新增 `app/api/train_admin.py`：
    - `POST /train/llm/start`：内部使用接口，接收训练配置并通过 `TrainingOrchestrator` 启动大模型训练任务；
    - `GET /train/llm/status`：查询指定 job 或全部任务的当前状态。
  - 更新 `app/main.py`：
    - 注册 `train_admin` 路由前缀 `/train`。

> 至此，TODO-P9 所需的“大模型训练/微调管理闭环”的企业级代码骨架已搭建完成：既支持 LLaMA-Factory 通道也支持代码训练通道，并通过统一 Orchestrator 与管理 API 做任务生命周期的基础管理，后续只需接入实际环境与持久化存储即可扩展为生产级方案。

## 2026-03-17 NL2SQL 专用 RAG 命名空间与摄入骨架（下一阶段工作清单 TODO-P11）实现 + 多步 Agent Workflow 骨架

- 更新 `app/nl2sql/rag_service.py`：
  - 为 NL2SQL 定义专用命名空间：
    - `nl2sql_schema`、`nl2sql_biz_knowledge`、`nl2sql_qa_examples`；
  - 提供对应的摄入方法：
    - `index_schema_snippets` / `index_biz_knowledge` / `index_qa_examples`；
  - `retrieve(question)` 会从上述多命名空间联合检索上下文片段并去重，作为 NL2SQLChain 的输入上下文。
- 更新 RAG 摄入服务与管理 API：
  - `app/rag/ingestion.py`：
    - `RAGDatasetMeta` 增加 `namespace` 字段；
    - `ingest_texts` 支持传入 `namespace`，并在向量库与元信息中记录。
  - `app/api/rag_admin.py`：
    - `/rag/ingest/texts` 请求体增加 `namespace` 字段，支持按命名空间摄入 NL2SQL 相关知识；
    - `/rag/datasets` 返回结果中增加 `namespace` 字段。
- 多步 Agent Workflow 骨架（蓝图落地补充）：
  - 更新 `app/nl2sql/chain.py`：
    - 在 `generate_sql(...)` 中增加 `_plan(...)` 步骤（当 LangChain 可用时）对自然语言问题进行 NL2SQL 规划（候选表/实体、关键字段、是否需要 join/聚合等），并将规划摘要用于增强 RAG 检索 query；
    - 在 SQL 初稿未通过 `SQLValidator` 校验时，若 LangChain 可用则调用 `_refine_sql(...)` 执行一轮自检与修正，再次校验后决定是否返回修正 SQL 或空字符串；
    - 保持原有接口与基本行为兼容，仅在有能力时启用多步 Agentic 流程。

> 至此，TODO-P11 与蓝图中 NL2SQL 多步 Workflow 的代码侧骨架已经对齐：既有多命名空间 RAG，又有规划与自检的 Agentic 扩展点，后续可以在此基础上接入真实 Schema 与评估闭环。


## 2026-03-17 性能与稳定性评估骨架（下一阶段工作清单 TODO-P12/P13）实现

- 新增压测脚本骨架：
  - `benchmarks/bench_llm_infer.py`：针对 `/llm/infer` 的并发压测示例；
  - `benchmarks/bench_chatbot.py`：针对 `/chatbot/chat`；
  - `benchmarks/bench_nl2sql.py`：针对 `/nl2sql/query`。
  - 上述脚本使用 `httpx.AsyncClient` 并发发送请求，统计 QPS、平均延迟、P95/P99 等基础指标，用于开发/测试环境快速评估性能。
- 新增性能评估文档骨架：
  - `docs/性能与稳定性评估.md`：
    - 定义了目标接口列表、压测工具与方法建议、指标维度、结果记录模板与调优记录模板；
    - 后续实际压测完成后可在该文档中补充具体结果与调优结论。

> 至此，TODO-P12/P13 在“代码与文档入口”的意义上已具备基础骨架，后续只需在目标环境运行这些脚本并记录结果，即可完成完整的性能与稳定性评估闭环。

## 2026-03-17 NL2SQL 场景下的 Agentic RAG 演进准备（下一阶段工作清单后续工作，骨架补充）

- 在 NL2SQL 链路中对 RAG 子系统的实现进行澄清与轻量补充：
  - 确认 `app/nl2sql/rag_service.py` 通过 `NL2SQLRAGService` 将 Schema/业务知识/Q&A 样例划分到 `nl2sql_schema` / `nl2sql_biz_knowledge` / `nl2sql_qa_examples` 三个命名空间，并在 `retrieve(...)` 中做多命名空间联合检索与去重；
  - 在 `app/nl2sql/chain.py` 中更新注释，明确 `generate_sql(...)` 调用 `_rag.retrieve(question)` 的含义是「面向 NL2SQL 的多命名空间 RAG 检索」，为后续在该位置插入 Agentic RAG 多步逻辑预留清晰切入点。
- 文档同步：
  - 在 `docs/大小模型应用技术架构与实现方案.md` “4.6 NL2SQL” 小节中补充说明，明确当前 NL2SQL RAG 已通过 `NL2SQLRAGService` 落地多命名空间检索骨架，下一步可在此基础上演进为 Agentic RAG（例如对用户问题进行澄清、按需追加补充检索等）。

## 2026-03-17 Agentic RAG 基座与综合分析/Chatbot 链路接入（下一阶段工作清单后续工作）实现

- 新增 Agentic RAG 基座：
  - 新增 `app/rag/agentic.py`：
    - 定义 `RAGMode`（`basic` / `agentic`）、`RAGContext` 与 `RAGResult` 数据结构；
    - 定义 `AgenticRAGService`，统一对外暴露 `retrieve(...)` 接口：
      - BASIC 模式直接委托现有 `RAGService` 完成单步检索；
      - AGENTIC 模式当前版本复用基础检索实现，仅在结果中标记 `used_agentic=True`，为后续多步规划与工具调用预留扩展点。
- 综合分析链路接入 Agentic RAG：
  - 更新 `app/llm/chains/analysis_chain.py`：
    - 在构造函数中基于传入/默认 `RAGService` 创建 `AgenticRAGService` 实例；
    - 在 `run(...)` 中，当启用 RAG 时通过 `AgenticRAGService.retrieve(...)`、并传入 `RAGContext(user_id, session_id, scene="analysis")` 与 `RAGMode.AGENTIC` 完成上下文检索；
    - 保持对外行为兼容（仍返回 `AnalysisResult.used_rag` 与 `context_snippets`），为后续引入真正的 Agent 多步流程打下基础。
- Chatbot 链路接入 Agentic RAG：
  - 更新 `app/llm/chains/chatbot_chain.py`：
    - 在构造函数中基于传入/默认 `RAGService` 创建 `AgenticRAGService` 实例；
    - 在 `run(...)` 中，当启用 RAG 时通过 `AgenticRAGService.retrieve(...)`、并传入 `RAGContext(user_id, session_id, scene="chatbot")` 与 `RAGMode.AGENTIC` 完成知识检索；
    - 对外仍保持原有入参与返回值不变，当前 Agentic 模式同样复用基础单步 RAG，实现“结构先行”的增强。
- 文档同步：
  - 更新 `docs/大小模型应用技术架构与实现方案.md` 的 RAG 分层与“4.3 智能客服”小节说明，明确当前已在代码中落地 `AgenticRAGService` 基座，并在综合分析与 Chatbot 场景启用，以支持后续演进为真正的多步 Agentic 工作流。

## 2026-03-17 多步 Agent Workflow 设计蓝图文档补充（统一规划推理 / 综合分析 / Chatbot / NL2SQL）

- 新增多步 Agent Workflow 设计蓝图文档：
  - 新增 `docs/Agentic-Workflow-设计蓝图.md`：
    - 针对四个核心业务场景给出统一的多步 Agent Workflow 规划：
      - 通用推理 `/llm/infer`：问题诊断 →（可选 Agentic RAG）→ 主回答生成；
      - 综合分析：分析任务规划 → 按子任务多轮 RAG 检索 → 多模态证据汇总 → 报告生成；
      - Chatbot：意图识别与路由 → 基于意图的 RAG 策略选择 → 主回复生成（以及后续跨业务路由扩展点）；
      - NL2SQL：问题理解与实体/表识别 → 基于规划结果的多命名空间 RAG 检索 → SQL 初稿生成 → SQL 自检与修正。
    - 明确 Agentic 基座（`AgenticRAGService` + `RAGMode`/`RAGContext`）与具体业务 Workflow 的职责边界：
      - 基座负责统一 RAG 能力与场景上下文管理；
      - 各业务链路（AnalysisChain / ChatbotChain / NL2SQLChain / LLMInferenceService）负责具体剧本（步骤顺序与业务语义）。
- 文档联动更新：
  - 在 `docs/大小模型应用技术架构与实现方案.md` 的相关小节中增加对蓝图文档的引用：
    - 在大模型推理（4.1）、智能客服（4.3）、综合分析（4.4）、NL2SQL（4.6）中，分别补充一句说明，指向 `docs/Agentic-Workflow-设计蓝图.md` 作为多步 Workflow 的详细设计依据。

## 2026-03-17 全局实现情况概览（阶段性小结）

- 代码层面：
  - 已按最初架构规划为各能力域（大模型推理/智能客服/综合分析/NL2SQL/小模型通道与训练/RAG/会话/训练管理/监控）落下企业级骨架代码：
    - 大模型：统一 LLM 配置与客户端、LangChain 链路（Chatbot/Analysis/NL2SQL/`/llm/infer`）、大模型训练双通道（LLaMA-Factory + 代码）及 TrainingOrchestrator；
    - 小模型：通道管理 + 多线程流水线、小模型注册表与训练服务（含 TensorBoard 与任务管理骨架）；
    - RAG/会话：通用 RAGService + 摄入服务 + NL2SQL 专用命名空间 + 会话存储（内存/Redis）；
    - NL2SQL：完整链路（Schema/RAG/Prompt/LLM 生成/校验/执行）及 API；
    - 监控与追踪：Prometheus 指标体系 + 可选 LangSmithTracker；
    - 管理与运维：`rag_admin`、`train_admin` 等内部管理接口。
  - 所有新增/重要模块均已通过 lints 检查，无明显语法或类型错误。

- 文档与 memory-bank：
  - `memory-bank`：
    - `00~04` 中的项目概述、架构、组件划分、开发流程与阶段进度仍然与当前实现保持一致；
    - `05-progress-log.md` 已按时间序列记录本阶段所有关键实现，现阶段可视为“统一事实源”的最新状态；
    - `06-copilot-instruction.md` 不需要修改，仍然正确反映对 AI 助手的总体约束与目标。
  - `docs`：
    - `下一阶段工作清单-未完成说明.md` 与 `下一阶段工作清单-未完成说明-实现说明.md` 已同步更新 P1~P3、P7~P9、P11~P13 的当前实现状态与后续建议，作为“代码已落地但仍需生产化验证”的清单；
    - 新增的 `docs/性能与稳定性评估.md` 已作为 P12/P13 的记录骨架存在；
    - 其它高层文档（例如《大小模型应用技术架构与实现方案》）仍与当前实现思路一致，仅在后续真正上线或调优完成后再进行细化补充即可。

> 总体上，本阶段的目标是“搭建统一技术基座的企业级代码骨架并同步维护 memory-bank/ 与 docs/”，该目标已达成；下一阶段的工作重心可转向：在具体运行环境中接入真实模型与基础设施、完成压测与调优，并据此对文档进行“从骨架到实测数据”的进一步完善。

## 2026-03-17 RAG 嵌入服务升级为真实嵌入模型（离线/在线配置化）

- **配置**（`app/core/config.py`）：
  - `RAGConfig` 中保留 `embedding_model_path`（离线模型目录）、`embedding_model_name`（在线模型名，默认 `BAAI/bge-small-zh-v1.5`）；已移除占位回退开关 `embedding_fallback_placeholder`。
- **嵌入服务实现**（`app/rag/embedding_service.py`）：
  - `EmbeddingService` 采用「离线优先、在线回退」策略：优先从 `EMBEDDING_MODEL_PATH` 加载，否则按 `EMBEDDING_MODEL_NAME` 在线下载；在线失败时仅打日志并抛出异常，无占位实现。
  - 依赖 `sentence_transformers`，由 `requirements-大模型应用.txt` 统一管理。
- **文档与依赖**：
  - 嵌入模型配置说明已合并至现有文档：`docs/大小模型应用技术架构与实现方案.md`（4.5 嵌入模型）、`framework-guide/框架架构与调用链路总览.md`、`memory-bank/02-components.md`；原独立文档 `docs/嵌入模型配置说明.md` 已删除。
  - 项目按业务域拆分依赖：大模型应用、小模型应用、大模型训练、小模型训练；嵌入依赖并入 `requirements-大模型应用.txt`，原 `requirements-embedding.txt` 已删除。



