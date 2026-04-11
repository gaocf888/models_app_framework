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

## 2026-03-26 小模型通道推理：按 algor_type 分发策略 + 证据保存 + 回调（Calling 40417）

- 将小模型通道推理从“占位版”升级为“可运行版”：
  - 新增 `configs/small_model_algorithms.yaml`：以 `algor_type` 为键的算法配置（策略类、默认权重、阈值、证据保存目录、回调地址等）。
  - 新增 `app/small_models/algorithm_registry.py`：加载算法配置并支持“API 覆盖本地配置”的合并逻辑。
  - 新增/改造 `app/small_models/strategy/*`：按算法类型封装策略实现；实现 `CallingStrategy`（algor_type=40417）并使用 `app/small_models/pretrained/call.pt` 权重（Ultralytics YOLO）。
  - 新增 `app/small_models/evidence.py`：保存证据帧图片（jpg）与触发后视频片段（mp4，post-roll 简化版）。
  - 新增 `app/small_models/callback_client.py`：将检测结果 + 证据路径回调到业务 Web 服务（若配置了 `callback_url`）。
  - 更新 `app/models/small_model.py`：补充 `weights_path/callback_url/evidence_dir/device/imgsz/conf/iou/cooldown_seconds/clip_seconds` 等可选字段，用于 API 覆盖配置。
  - 更新 `app/services/small_model_channel_service.py`：修复 `algor_type` 被覆盖丢失的问题，并把上述可选字段透传到通道 `extra_params`。
  - 更新 `app/small_models/workers.py`：
    - decoder 入队改为 `put(timeout=...)` 背压，去除 `full()+sleep` 忙等；
    - inference 调用升级为 `SmallModelInferenceEngine.infer(channel_id, model_name, frame_item, api_overrides=...)`。

> 备注：当前视频片段保存为简化实现（触发后录制 N 秒），后续可按业务需要增加 pre-roll（触发前 N 秒）缓存与更完整的帧率/时间戳对齐。

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



## 2026-03-26 传统 RAG 企业级改造（ES/EasySearch + 同名更新 + 混合检索重排）

- 配置升级（`app/core/config.py`）：
  - `RAGConfig` 新增 `es`（ElasticsearchConfig）与 `hybrid`（HybridRetrievalConfig）配置，支持 `RAG_VECTOR_STORE_TYPE=es|easysearch|faiss|memory`。
  - 新增环境变量：`RAG_ES_*`（地址/认证/索引）、`RAG_HYBRID_*`（双路召回、RRF 参数、Top-N）、`RAG_RERANKER_MODEL_*`（CrossEncoder 模型名/本地路径）。
- 存储升级（`app/rag/vector_store.py`）：
  - `VectorStore` 抽象扩展为：`add_texts`（含 `doc_name/metadata`）、`keyword_search`、`delete_by_doc_name`。
  - 新增 `ElasticsearchVectorStore`，用于 ES/EasySearch 的“向量+全文”统一存储与检索。
  - `FaissVectorStore` 增加关键词检索回退与按 `doc_name` 删除；修复删除后 internal_id 对齐问题（改为稳定 id 映射持久化）。
- 摄入升级（`app/rag/ingestion.py` + `app/api/rag_admin.py`）：
  - 摄入接口新增 `doc_name`、`replace_if_exists`；默认按“同名文档先删后灌”处理更新问题。
  - 数据集元数据新增 `doc_name` 字段，便于运维查询与审计。
- 检索升级（`app/rag/rag_service.py`）：
  - `retrieve_context` 支持 `namespace` 与混合检索开关；
  - 实现“语义召回 + 关键词召回”并行、多路融合（RRF）与 CrossEncoder 重排（默认 `BAAI/bge-reranker-large`）。
  - 新增 `delete_by_doc_name` 管理接口，供摄入更新流程复用。
- 管理面与稳态增强：
  - `app/api/rag_admin.py` 新增批量摄入接口 `/rag/ingest/documents` 与按文档删除接口 `/rag/documents/delete`；
  - `app/rag/vector_store.py` 的 ES/EasySearch 操作加入基础重试机制（固定重试 3 次）；
  - 新增 RAG 可观测指标：语义召回次数、关键词召回次数、重排次数、文档删除计数（见 `app/core/metrics.py`）。
- 自动化回归：
  - 新增 `tests/test_rag_core.py`，覆盖 RRF 融合与 InMemory 文档删除两个核心回归点；
  - 通过 `python -m unittest discover -s tests -p "test_*.py"` 验证通过。

## 2026-03-26 场景化检索策略配置接入（LLM/Chatbot/Analysis/NL2SQL）

- 配置升级（`app/core/config.py`）：
  - 新增 `RAGSceneProfile` / `RAGSceneProfilesConfig`，支持按场景配置 `top_k/semantic_top_k/keyword_top_k/rerank_top_n`；
  - 新增环境变量前缀：`RAG_SCENE_LLM_*`、`RAG_SCENE_CHATBOT_*`、`RAG_SCENE_ANALYSIS_*`、`RAG_SCENE_NL2SQL_*`。
- 检索核心（`app/rag/rag_service.py`）：
  - `retrieve_context` 增加 `scene` 参数，可按场景读取检索参数；
  - 在不指定场景时保持全局配置默认行为，确保兼容历史调用。
- 调用链接入：
  - `app/rag/agentic.py`：检索时透传 `ctx.scene` 与 `namespace`；
  - `app/nl2sql/rag_service.py`：检索调用显式指定 `scene="nl2sql"`；
  - 通用推理/客服/分析链路通过 `RAGContext.scene` 自动生效。

## 2026-03-26 RAG API 端到端脚本 + ES migration 版本化方案

- 新增脚本目录与文件：
  - `app/test_scripts/rag/README.md`
  - `app/test_scripts/rag/rag_api_e2e.py`
- 脚本覆盖链路：
  - ingest（首次摄入）→ query（检索验证）→ update（同名文档重灌）→ delete（按文档删除）
- API 增强（`app/api/rag_admin.py`）：
  - 新增 `POST /rag/query`，便于管理面与自动化脚本直接验证检索结果；
  - `ingest/query/delete` 增加明确异常日志与 HTTP 500 错误返回（轻量失败补偿策略）。
- ES migration（`app/rag/vector_store.py`）：
  - 新增“版本化物理索引 + alias 访问”策略；
  - 通过 `RAG_ES_INDEX_NAME` + `RAG_ES_INDEX_VERSION` 构造物理索引，业务访问统一走 `RAG_ES_INDEX_ALIAS`；
  - 启用 `RAG_ES_AUTO_MIGRATE_ON_START=true` 时自动执行 alias 切换；
  - 默认不删旧索引，避免误删，数据回灌由摄入任务或离线 reindex 处理。

## 2026-03-26 RAG 上线门禁记录模板（与 framework-guide 清单对齐）

> 用途：每次 RAG 版本上线前，按本模板记录“是否满足上线门禁”。  
> 填写建议：将“通过/不通过”与证据（压测报告、监控截图、日志链接）一起记录，便于审计与回溯。

### A. 必须项（Blocking）

| 检查项 | 结果（通过/不通过） | 证据与备注 |
|------|------|------|
| ingest/query/update/delete 全链路验证通过 |  |  |
| 向量+全文双存储可用（ES/EasySearch） |  |  |
| 同名文档更新（先删后灌）验证通过 |  |  |
| 混合检索 + 重排效果验证通过 |  |  |
| 配置化参数在目标环境生效 |  |  |
| ES 版本索引 + alias 迁移验证通过 |  |  |
| API 异常日志与错误返回验证通过 |  |  |
| 自动化测试（单测 + E2E）执行通过 |  |  |
| `/rag/*` 管理接口鉴权与审计就绪 |  |  |
| 压测达标（QPS/P95/P99/错误率） |  |  |
| 回滚预案演练通过（alias 回切） |  |  |

### B. 建议项（Strongly Recommended）

| 检查项 | 结果（通过/不通过） | 证据与备注 |
|------|------|------|
| Grafana/Loki 告警策略已配置 |  |  |
| 旧索引生命周期治理策略已配置 |  |  |
| 多环境参数基线（dev/staging/prod）已固化 |  |  |
| 批处理摄入重试/重跑机制可用 |  |  |
| 数据质量巡检机制（命中率/重排收益）可用 |  |  |

### C. 上线结论

- 上线版本：  
- 上线环境：  
- 结论（允许上线/延期）：  
- 责任人：  
- 时间：

## 2026-03-26 标准文档摄入链路补齐（服务端清洗+切块）

- 新增 `app/rag/document_pipeline.py`：
  - 提供 `DocumentPipeline`（normalize + chunk）；
  - 支持 `chunk_size/chunk_overlap/min_chunk_size` 参数化切块。
- `app/api/rag_admin.py` 新增原始文档摄入接口：
  - `POST /rag/ingest/raw_document`
  - `POST /rag/ingest/raw_documents`
- 保留原有接口定位：
  - `/rag/ingest/texts` 与 `/rag/ingest/documents` 继续面向“已分块输入”；
  - raw 接口面向“原始文档输入”，由服务端完成清洗与切块后再入库。

## 2026-03-27 企业级摄入主链路第一阶段落地（按设计稿）

- 新增 `app/rag/models.py`：
  - 定义 `DocumentSource`、`ChunkRecord`、`IngestionJob`、`IngestionJobStatus`。
- 文档处理升级为模块化包 `app/rag/document_pipeline/`：
  - `parsers.py`、`cleaners.py`、`splitters.py`、`enrichers.py`、`pipeline.py`；
  - 实现 parser + cleaner + structure/semantic/window splitter + chunk metadata/hash。
- 新增编排器 `app/rag/ingestion_orchestrator.py`：
  - 实现异步任务提交、状态机、步骤推进、失败/部分失败、任务重试；
  - 任务状态持久化到 `./data/rag_jobs/jobs.json`。
- 新增 migration 模块 `app/rag/migrations/index_migrator.py`：
  - 提供索引版本切换与 alias 回滚能力。
- 管理 API 扩展（`app/api/rag_admin.py`）：
  - 新增 `POST /rag/jobs/ingest`、`GET /rag/jobs/{job_id}`、`POST /rag/jobs/{job_id}/retry`；
  - 新增 `POST /rag/documents/upsert` 同步快速通道。

## 2026-03-27 企业级摄入主链路第二阶段（docs/jobs 索引与运维字段）

- 新增结构化仓库：
  - `app/rag/job_repository.py`：任务元数据写入 jobs 索引（ES）或文件回退；
  - `app/rag/document_repository.py`：文档级元数据写入 docs 索引（ES）或文件回退。
- 编排器增强（`app/rag/ingestion_orchestrator.py`）：
  - 任务过程中同步写入 job/doc 记录；
  - 增加 step 耗时统计（`metrics.step_durations_ms`）；
  - 增加 doc 级错误码记录（如 `E_CHUNK_EMPTY`）。
- 测试补充：
  - 新增 `tests/test_ingestion_orchestrator.py`，覆盖 SUCCESS 与 PARTIAL 两类状态流转。

## 2026-03-27 企业级摄入主链路第三阶段（运维 API 与迁移管理）

- `app/api/rag_admin.py`：
  - 增加 job 响应模型标准化（`IngestionJobInfo` 等），统一任务查询返回结构；
  - 新增 `GET /rag/jobs` 分页接口（`limit/offset`）；
  - 新增迁移管理接口：
    - `POST /rag/migrations/chunks/run`
    - `POST /rag/migrations/chunks/rollback`
- `app/rag/ingestion_orchestrator.py`：
  - 新增 `count_jobs()` 供分页总数统计；
  - 保持列表按创建时间倒序输出。
- 测试增强：
  - `tests/test_ingestion_orchestrator.py` 新增 list/count 覆盖用例。
- 依赖升级：
  - `requirements-大模型应用.txt` 新增 `elasticsearch` 依赖，用于 ES/EasySearch 客户端。

## 2026-03-27 企业级摄入主链路第四阶段（索引查询 API 与 E2E 迁移验证）

- 元数据仓库查询能力增强：
  - `app/rag/job_repository.py` 新增 `list(limit, offset)`；
  - `app/rag/document_repository.py` 新增 `get(doc_key)`、`list(limit, offset, namespace)`；
  - ES/EasySearch 与本地 JSON 回退模式均可用，统一按时间倒序分页。
- 管理 API 扩展（`app/api/rag_admin.py`）：
  - 新增 `GET /rag/jobs/{job_id}/documents`：按任务查看关联文档列表；
  - 新增 `GET /rag/documents/meta`：分页查询文档级元数据（chunk 数、状态、错误、更新时间）。
- E2E 脚本升级（`app/test_scripts/rag/rag_api_e2e.py`）：
  - 增加可选 migration 验证：`--test-migration --migration-dim`；
  - 覆盖 `POST /rag/migrations/chunks/run` 与 `POST /rag/migrations/chunks/rollback`，
    并在 rollback 验证后恢复 alias 到新索引，降低对环境的干扰。
- 文档同步：
  - `app/test_scripts/rag/README.md` 增补 migration 测试参数说明；
  - `framework-guide/RAG整体实现技术说明.md` 增补第四阶段新增 API 说明。

## 2026-03-27 企业级摄入主链路第五阶段（管理 API 测试补齐与初始化治理）

- 新增管理 API 单元测试：
  - `tests/test_rag_admin_api.py`，覆盖：
    - `GET /rag/jobs/{job_id}/documents` 成功与 404 路径；
    - `GET /rag/documents/meta` 分页与 namespace 过滤参数透传。
- `app/api/rag_admin.py` 依赖初始化方式升级为懒加载：
  - 新增 `_get_service()`、`_get_orchestrator()`、`_get_job_repo()`、`_get_doc_repo()`（`lru_cache`）；
  - 避免模块导入阶段即加载 embedding 模型，降低冷启动与测试环境耦合风险；
  - 保持接口行为不变。
- 测试执行结果：
  - `python -m unittest tests.test_rag_admin_api tests.test_ingestion_orchestrator tests.test_rag_core`
  - 结果 `OK`（8 tests）。

## 2026-03-27 企业级摄入主链路第六阶段（migration 回归测试与运维排障）

- migration 单测补齐：
  - 新增 `tests/test_index_migrator.py`，覆盖
    - `ensure_index_and_alias()` 的 alias 切换动作；
    - `rollback_alias()` 的回滚动作；
  - 使用 fake ES client 验证 remove/add action 语义，避免依赖真实 ES 环境。
- 迁移器可测试性增强：
  - `app/rag/migrations/index_migrator.py` 支持注入可选 client（生产默认仍使用官方 ES 客户端）。
- 运维文档增强：
  - `framework-guide/RAG整体实现技术说明.md` 增补“运维排障与回滚手册”，包含错误码解释、标准排查路径、迁移回滚路径。

## 2026-03-27 企业级摄入主链路第七阶段（migration 一致性自动回归）

- 新增 E2E 回归脚本：
  - `app/test_scripts/rag/rag_migration_consistency_e2e.py`
  - 覆盖：基线摄入/查询 → migration run → 迁移后查询一致性校验 →（可选）rollback 后一致性校验 → 恢复 alias。
- 一致性验收口径：
  - 对固定 query 集合，比较迁移前后 `top_k` 结果文本 overlap ratio；
  - 默认阈值 `0.6`，可通过参数 `--consistency-threshold` 调整。
- 文档同步：
  - `app/test_scripts/rag/README.md` 增加脚本说明与执行命令；
  - `framework-guide/RAG整体实现技术说明.md` 增加一致性回归脚本与验收口径说明。

## 2026-03-27 企业级摄入主链路第八阶段（基线样本配置化）

- 一致性回归脚本配置化升级：
  - `app/test_scripts/rag/rag_migration_consistency_e2e.py` 新增 `--cases-file`；
  - 默认读取 `app/test_scripts/rag/migration_consistency_cases.json`；
  - 支持按环境维护独立样本集（namespace/dataset/documents/queries）。
- 新增样本文件：
  - `app/test_scripts/rag/migration_consistency_cases.json`。
- 文档同步：
  - `app/test_scripts/rag/README.md` 增加样本文件驱动说明；
  - `framework-guide/RAG整体实现技术说明.md` 增补 `--cases-file` 使用约定。

## 2026-03-27 企业级摄入主链路第九阶段（多场景评测与 CI 报告）

- `rag_migration_consistency_e2e.py` 升级：
  - query case 支持对象结构：`query + scene + top_k`；
  - 支持 `--report-out` 输出 JSON 评测报告（包含 phase/ratio/passed）。
- 默认样本扩展：
  - `migration_consistency_cases.json` 增加多场景 query（`llm_inference/analysis/chatbot`）。
- 文档同步：
  - `app/test_scripts/rag/README.md` 增加多场景与报告输出说明；
  - `framework-guide/RAG整体实现技术说明.md` 增加 `--report-out` 用途说明（CI 门禁）。

## 2026-03-27 企业级摄入主链路第十阶段（Markdown 汇总报告）

- `rag_migration_consistency_e2e.py` 增加 `--report-md-out`：
  - 在 JSON 结构化报告之外，输出 Markdown 汇总；
  - 包含阈值、总检查数、失败数、按 phase/scene/query 的结果表格。
- 文档同步：
  - `app/test_scripts/rag/README.md` 增加 Markdown 报告示例命令；
  - `framework-guide/RAG整体实现技术说明.md` 增加 `--report-md-out` 说明。

## 2026-03-27 企业级摄入主链路第十一阶段（失败时报告与退出码联动）

- `rag_migration_consistency_e2e.py`：
  - `try/except/finally`：失败时仍写入 JSON/Markdown（当指定输出路径时）；
  - 报告字段：`status`（success/failed）、`failed_phase`、`error`、`error_type`、`traceback`；
  - 成功迁移后写入 `migration`（`old_indices` / `new_index`）便于对照；
  - 进程仍以非零退出码结束（便于 CI 判失败）。
- 文档同步：`app/test_scripts/rag/README.md`、`framework-guide/RAG整体实现技术说明.md`。

## 2026-03-27 设计稿对齐（摄入配置 / RetrievedChunk / job_type）

- `app/core/config.py`：新增 `RAGIngestionConfig`，挂载到 `RAGConfig.ingestion`，环境变量与设计稿 §4 对齐（`RAG_INGEST_*`、`RAG_PIPELINE_VERSION`、`RAG_CHUNK_*`、`RAG_CLEANING_PROFILE` 等）。
- `app/rag/models.py`：`IngestionJobType`、`RetrievedChunk`；`IngestionJob` 增加 `job_type`（默认 `upsert`）。
- `app/rag/rag_service.py`：`retrieve_chunks()` 返回标准 `RetrievedChunk`；`retrieve_context()` 委托前者。
- `app/rag/agentic.py`：`RAGResult.chunks`；检索走 `retrieve_chunks`。
- `app/rag/ingestion_orchestrator.py`：线程池并发与默认 `ChunkingConfig`、文档侧 `pipeline_version` 取自 `RAGIngestionConfig`；任务 JSON 增加 `job_type`。
- `app/api/rag_admin.py`：`IngestionJobInfo` 增加 `job_type`。
- `enterprise-level_transformation_docs/企业级 RAG 文档摄入与检索一体化改造设计稿-20260327.md`：附录 A 实现对照。
- `framework-guide/RAG整体实现技术说明.md`：§4 增补摄入平台环境变量表与 `RetrievedChunk` 说明。

## 2026-03-27 设计稿对齐推进（GraphRAG 从骨架到可运行闭环）

- `app/graph/ingestion.py`：
  - 实现规则实体抽取（中英混合）；
  - 写入 `DocumentChunk` / `Entity` 节点；
  - 写入 `MENTION` 与 `CO_OCCUR` 关系（含权重累加）。
- `app/graph/query_service.py`：
  - 实现 query 实体抽取；
  - 支持按 namespace 查询实体相关文档片段；
  - 当 `max_hops>=2` 时补充共现关系事实召回；
  - 输出可直接拼接到 RAG 上下文的事实文本列表。
- 兼容性：
  - 兼容不同 Neo4jGraph 版本的 `query/run` 调用方式；
  - 现有 HybridRAGService 无需改接口即可消费图事实。

## 2026-03-27 设计稿对齐推进（幂等键与版本治理）

- 领域模型扩展：
  - `DocumentSource` 增加 `doc_version`（默认 `v1`）与 `tenant_id`；
  - `IngestionJob` 增加 `idempotency_key`。
- 编排器增强（`app/rag/ingestion_orchestrator.py`）：
  - 新增幂等键参数 `idempotency_key`（显式提供时启用复用，避免重复运行中的任务）；
  - 默认自动生成幂等摘要并随 job 持久化，支持审计；
  - docs 元数据键升级为 `tenant::namespace::doc_name::doc_version`；
  - 持久化 payload 写入 `doc_version/tenant_id`；
  - 调用摄入服务时透传 `doc_version/tenant_id`，并保留对旧 mock 签名兼容。
- 摄入服务增强（`app/rag/ingestion.py`）：
  - `ingest_texts` 增加 `doc_version/tenant_id` 入参；
  - chunk metadata 写入 `doc_version/tenant_id`。
- 管理 API 增强（`app/api/rag_admin.py`）：
  - jobs ingest 请求新增 `idempotency_key`；
  - job/doc 响应补充 `idempotency_key`、`doc_version`、`tenant_id` 字段。

## 2026-03-27 设计稿对齐推进（文档处理层企业级增强）

- `app/rag/document_pipeline/parsers.py`：
  - `pdf/docx` 增加本地文件路径解析能力（`file://` 或绝对路径）；
  - 保留旧兼容：若非文件路径则按“已提取文本”处理。
- `app/rag/document_pipeline/cleaners.py`：
  - 清洗档位化：`strict/normal/light`；
  - 增加目录/页码噪音行清理与严格档位符号行压缩。
- `app/rag/document_pipeline/pipeline.py`：
  - 接入 `RAGIngestionConfig` 默认参数；
  - 支持 `RAG_DEFAULT_CHUNK_STRATEGY`（`structure/semantic/window`）与 `RAG_CLEANING_PROFILE`。
- 依赖与文档：
  - `requirements-大模型应用.txt` 新增 `pypdf`、`python-docx`；
  - `framework-guide/RAG整体实现技术说明.md` 同步新增文档处理实现说明。

## 2026-03-27 设计稿对齐推进（NL2SQL 标准 chunk 结构）

- `app/nl2sql/rag_service.py`：
  - 新增 `retrieve_chunks()`，返回标准 `RetrievedChunk`；
  - 兼容 `retrieve()` 仍返回 `List[str]`，但改为由结构化 chunk 渲染，保留来源线索（namespace/doc/section）。
- `framework-guide/RAG整体实现技术说明.md`：
  - 补充 NL2SQL 已支持标准 `RetrievedChunk` 的说明。

## 2026-03-27 设计稿对齐推进（AgenticRAG 多步计划检索）

- `app/rag/agentic.py`：
  - AGENTIC 模式从“占位回退”升级为多步策略：
    - 子问题规划（主问题 + 连接词拆分 + 场景强化查询）；
    - 子问题并行检索（线程池）；
    - 融合策略（score + rank bonus + step weight）；
    - 去重与预算裁剪（按 chunk_id/text）。
  - `RAGResult` 增加 `plan_steps`，支持链路 trace。
- 验证：
  - 现有回归测试通过（`test_rag_core` / `test_rag_admin_api` / `test_ingestion_orchestrator` / `test_index_migrator`）。

## 2026-03-27 设计稿对齐推进（Agentic 策略配置化与注释完善）

- `app/core/config.py`：
  - 新增 `RAGAgenticConfig`，参数外置并接入环境变量（`RAG_AGENTIC_*`）。
- `app/rag/agentic.py`：
  - 读取 `RAGAgenticConfig`，将子问题数、并发、预算与融合权重改为可配置；
  - 增加关键注释（开关回退、主问题优先、场景增强可开关、并发预算语义）。
- `framework-guide/RAG整体实现技术说明.md`：
  - 补充 Agentic 多步策略参数表，方便运维调参。

## 2026-03-27 设计稿对齐推进（GraphRAG 策略配置化）

- `app/core/config.py`：
  - 扩展 `GraphRAGConfig`，新增实体抽取阈值、每 chunk 实体上限、共现事实最小权重、事实模板等参数；
  - `_load_from_env` 新增 `GRAPH_ENTITY_*`、`GRAPH_MAX_ENTITIES_PER_CHUNK`、`GRAPH_MIN_COOCCUR_WEIGHT`、`GRAPH_FACT_TEMPLATE_*` 的环境变量解析。
- `app/graph/ingestion.py`：
  - `_extract_entities` 改为读取配置驱动的中英文长度阈值与实体上限，降低硬编码。
- `app/graph/query_service.py`：
  - 图事实输出改为模板驱动；
  - 共现事实增加最小权重阈值过滤；
  - 查询词抽取与摄入侧统一长度阈值口径。
- `framework-guide/RAG整体实现技术说明.md`：
  - 将 GraphRAG 从“骨架说明”更新为“轻量可用实现 + 参数化策略”，并补充新增环境变量说明。

## 2026-03-27 设计稿对齐推进（Graph 与 Hybrid 深度融合）

- `app/rag/hybrid_rag_service.py`：
  - 新增轻量意图路由 `_route_strategy`（受 `GRAPH_RAG_USE_INTENT_ROUTING` 控制）；
  - 对关系/依赖类问题自动提升图侧权重并提高 `graph_hops`，对定义类问题回调向量侧权重；
  - `hybrid` 融合从“顺序拼接”升级为“权重配额 + 交织合并”，减少单通道上下文挤占；
  - `graph`/`hybrid` 执行链路统一接收路由后的 `graph_hops/max_graph_items/weights`，保持参数语义一致。
- `tests/test_hybrid_rag_service.py`：
  - 新增 HybridRAGService 单测，覆盖权重交织与意图路由触发行为。
- `framework-guide/RAG整体实现技术说明.md`：
  - 补充 HybridRAGService 的意图路由与交织融合行为说明。

## 2026-03-27 设计稿对齐推进（Hybrid 意图路由完全配置化）

- `app/core/config.py`：
  - 扩展 `GraphHybridStrategyConfig`，新增意图关键词（中英）与路由后权重/hops/max_graph_items 参数；
  - `_load_from_env` 新增 `GRAPH_RAG_RELATION_KEYWORDS*`、`GRAPH_RAG_DEFINITION_KEYWORDS*`、
    `GRAPH_RAG_ROUTED_RELATION_*`、`GRAPH_RAG_ROUTED_DEFINITION_*` 的环境变量解析。
- `app/rag/hybrid_rag_service.py`：
  - `_route_strategy` 移除硬编码关键词与阈值，改为完全读取配置；
  - 新增 `_contains_keywords` 统一关键词命中判断，提升可维护性。
- `tests/test_hybrid_rag_service.py`：
  - 新增“自定义关键词触发路由”单测，验证路由规则已可配置。
- `framework-guide/RAG整体实现技术说明.md`：
  - 补充新增 Graph/Hybrid 路由参数的环境变量说明。

## 2026-03-27 设计稿对齐推进（统一 RetrievalPolicy 策略层）

- `app/rag/retrieval_policy.py`（新增）：
  - 新增 `RetrievalPolicy` 与 `RetrievalDecision`，统一承载 query 到检索参数的路由决策（mode/weights/hops/max_graph_items）。
- `app/rag/hybrid_rag_service.py`：
  - 移除内联 `_route_strategy`，改为调用 `RetrievalPolicy.decide()`，减少策略重复。
- `app/services/chatbot_service.py` 与 `app/services/analysis_service.py`：
  - 在非 LangChain 回退链路改为走 `HybridRAGService.retrieve()`，确保业务入口也复用统一策略层。
- `tests/test_retrieval_policy.py`（新增）：
  - 覆盖“关系类触发图增强路由”与“定义类回调向量权重”场景。
- `framework-guide/RAG整体实现技术说明.md`：
  - 补充 `RetrievalPolicy` 组件职责与接入说明。

## 2026-03-27 设计稿对齐推进（LLMInference 统一入口兼容接入）

- `app/services/llm_inference_service.py`：
  - 新增 `HybridRAGService` 注入，`rag_mode=basic` 下改走统一策略入口（Hybrid -> RetrievalPolicy）；
  - `rag_mode=agentic` 仍保留原有 `AgenticRAGService` 多步策略链路，不做降级替换。
- `framework-guide/RAG整体实现技术说明.md`：
  - 更新 `/llm/infer` 调用路径说明，明确 basic 与 agentic 的分流行为。

## 2026-03-27 设计稿对齐推进（Graph 生命周期一致性收口）

- `app/rag/ingestion.py`：
  - 新增 `post_index_hook`，承接图侧写入，形成“索引完成后统一后置扩展点”；
  - `ingest_texts` 新增 `run_post_hook` 开关，支持由 orchestrator 统一调度 hook；
  - `delete_by_doc_name` 增加图侧同步删除调用（向量删与图删一致）。
- `app/rag/ingestion_orchestrator.py`：
  - 索引完成后显式调用 `post_index_hook`；
  - 对旧 mock 保持兼容（无 `run_post_hook` / 无 `post_index_hook` 不报错）。
- `app/graph/ingestion.py`：
  - `ingest_from_chunks` 增加 `doc_name/doc_version/replace_if_exists`；
  - 图侧同名同版本重灌前先清理旧文档图节点；
  - `DocumentChunk` 增加 `doc_name/doc_version/doc_key`，并提供 `delete_document` 清理与孤立实体回收。
- `tests/test_ingestion_service_hooks.py`（新增）：
  - 覆盖 post_index_hook 触发图写入与 delete 同步图清理。
- `framework-guide/RAG整体实现技术说明.md`：
  - 补充 Graph 摄入后置 hook 与 doc_version 一致性、删除一致性说明。

## 2026-03-27 设计稿对齐推进（NL2SQL 接入统一策略层）

- `app/nl2sql/rag_service.py`：
  - 接入 `RetrievalPolicy` 决策检索模式；
  - 在保持 `RetrievedChunk` 契约不变的前提下，支持按策略补充图事实 chunk；
  - 保持 schema/biz/qa 三命名空间联合检索与去重逻辑。
- `tests/test_nl2sql_rag_service.py`（新增）：
  - 覆盖 vector 模式与 graph 模式下的 NL2SQL 检索行为。
- 文档：
  - `framework-guide/RAG整体实现技术说明.md` 与 `framework-guide/NL2SQL整体实现技术说明.md` 同步更新为“NL2SQL 已接入统一策略层”。

## 2026-03-27 设计稿对齐推进（文档生命周期 E2E 脚本）

- `app/test_scripts/rag/rag_doc_lifecycle_e2e.py`（新增）：
  - 覆盖“同文档名多版本异步 upsert（v1->v2）→ 检索替换验证 → 元数据验证 → 删除验证”全链路；
  - 重点验证 replace_if_exists 生效后旧版本内容不可检索；
  - 删除后通过查询为空验证向量/图侧同步清理链路。
- `app/test_scripts/rag/README.md`：
  - 补充新脚本用途与运行示例。

## 2026-03-27 设计稿对齐推进（管理脚本与 CI 报告增强）

- `app/manage_scripts/rag_doc_lifecycle_admin.py`（新增）：
  - 新增文档生命周期管理脚本（与测试脚本分离）；
  - 支持按 JSON 计划执行 `upsert/delete`、`dry-run`、`fail-fast`、任务轮询与执行报告输出。
- `app/manage_scripts/README.md`、`app/manage_scripts/examples/rag_doc_lifecycle_plan.json`（新增）：
  - 提供管理脚本说明与可直接复用的计划样例。
- `app/test_scripts/rag/rag_doc_lifecycle_e2e.py`：
  - 增加与 migration 一致的 JSON/Markdown 报告输出能力；
  - 失败时保留 `failed_phase/error/error_type/traceback`，可直接接 CI 门禁。
- `app/test_scripts/rag/README.md`：
  - 同步补充生命周期脚本的 `--report-out/--report-md-out` 用法。

## 2026-03-27 设计稿对齐推进（企业级清洗能力补齐）

- `app/rag/document_pipeline/cleaners.py`：
  - 在原 `strict/normal/light` 基础上补齐企业级清洗能力：
    - 跨页重复页眉/页脚识别与清理（基于分页首尾行重复统计）；
    - 重复段落合并；
    - 常见编码噪音修复（mojibake 片段与坏字符清理）；
    - 目录噪音规则增强（点线目录行等）。
- `app/core/config.py`：
  - `RAGIngestionConfig` 新增清洗策略开关与阈值参数：
    `RAG_CLEAN_REMOVE_HEADER_FOOTER`、
    `RAG_CLEAN_MERGE_DUPLICATE_PARAGRAPHS`、
    `RAG_CLEAN_FIX_ENCODING_NOISE`、
    `RAG_CLEAN_MIN_REPEATED_LINE_PAGES`。
- `app/rag/document_pipeline/pipeline.py`：
  - `DocumentPipeline` 将上述清洗参数统一注入 `TextCleaner`，实现配置化运行。
- `tests/test_text_cleaner.py`（新增）：
  - 覆盖页眉页脚清理、重复段合并、编码噪音修复三类核心能力。
- `framework-guide/RAG整体实现技术说明.md`：
  - 补充新增清洗环境变量与企业级清洗能力说明。

## 2026-03-27 设计稿对齐推进（Orchestrator 8 步显式状态机）

- `app/rag/ingestion_orchestrator.py`：
  - 将原 `parse_clean_chunk -> index -> finalize` 升级为 8 步显式状态机：
    `validate_input -> parse -> clean -> chunk -> enrich -> index -> quality_check -> finalize_alias_version`；
  - 新增 `_validate_document` 与 `_quality_check`，将质量门禁纳入显式治理步骤；
  - 新增 step 级耗时写入（`step_durations_ms`）与统一 step 状态更新函数。
- `app/rag/document_pipeline/pipeline.py`：
  - 新增 `process_document_staged()`，输出分阶段产物与阶段耗时，供 orchestrator 做 step 级治理。
- `app/rag/ingestion.py`：
  - 新增 `finalize_alias_version()` 扩展点（当前 no-op），对齐设计稿 finalize(alias/version) 阶段语义。
- `tests/test_ingestion_orchestrator.py`：
  - 增加对新 step 耗时记录（`parse` / `quality_check`）的断言。
- `framework-guide/RAG整体实现技术说明.md`：
  - 同步更新 `/rag/jobs/ingest` 的 8 步执行说明。

## 2026-03-27 设计稿对齐推进（Metadata Recall 通道）

- `app/rag/vector_store.py`：
  - 为 `VectorStore` 抽象新增 `metadata_search()`；
  - InMemory/Faiss/Elasticsearch 三种实现同步支持 metadata 召回（基于 `doc_name/doc_version/tenant_id` 等元信息）。
- `app/rag/rag_service.py`：
  - 在 hybrid 检索中新增 metadata 第三通道（semantic + keyword + metadata）；
  - RRF 融合纳入 metadata hits，补充 `RAG_METADATA_RECALL_COUNT` 指标。
- `app/core/config.py`：
  - `HybridRetrievalConfig` 新增 `metadata_top_k` 与 `metadata_recall_enabled`，并接入环境变量。
- `tests/test_metadata_recall.py`（新增）：
  - 覆盖 metadata 通道可独立返回检索结果。
- `framework-guide/RAG整体实现技术说明.md`：
  - 增加 `RAG_HYBRID_METADATA_TOP_K` / `RAG_HYBRID_METADATA_RECALL_ENABLED` 说明。

## 2026-03-27 设计稿对齐推进（问题修复回合）

- 修复 `FaissVectorStore` 分支潜在缺陷：
  - `keyword_search/delete_by_doc_name` 从错误的 `enumerate(self._items)` 改为按 `self._items.items()` 遍历；
  - 避免 dict key/value 混淆导致的异常与误删风险。

## 2026-03-27 文档同步（EasySearch 部署包接入）

- 新增 `rag_db-deploy/` 部署包（Docker Compose + 配置模板 + 初始化脚本 + 项目 env 模板）后，同步更新文档口径：
  - `framework-guide/数据持久化与容器部署说明.md`：
    - 从“FAISS 为主”调整为“ES/EasySearch 为主，FAISS 可选”；
    - 新增 `rag_db-deploy/README.md` 作为容器部署入口；
    - 补充默认 ES 连接参数与持久化建议。
  - `framework-guide/RAG整体实现技术说明.md`：
    - 顶部“配套文档”增加 `rag_db-deploy/README.md` 引用。
  - `docs/大小模型应用技术架构与实现方案.md`：
    - 将容器化部署说明同步为“默认 ES/EasySearch，兼容 FAISS”，并补充部署包引用。
- 删除能力增强：
  - `delete_by_doc_name` 全链路新增可选 `doc_version` 参数（API -> Service -> Store -> Graph delete）；
  - `POST /rag/documents/delete` 支持按版本精确删除。
- metadata recall 可用性增强：
  - metadata 通道分词从“仅空格 split”升级为中英混合 token 提取（适配中文查询词）。
- 测试补充：
  - `tests/test_rag_core.py` 增加按 `doc_version` 删除单测；
  - `tests/test_metadata_recall.py` 增加中文 metadata 查询命中单测。

## 2026-03-27 文档同步（RAG 快速阐述章节）

- `framework-guide/RAG整体实现技术说明.md`：
  - 在文档最上方新增“§0 快速阐述版（给开发者/客户）”，包含：
    - 一句话方案概括；
    - 策略分层图（摄入治理/检索策略/路由策略/编排策略）；
    - 客户可讲述要点；
  - 同步修正过时描述（如默认 FAISS、Agentic 等同 BASIC、Graph 骨架/待实现等）。
- 全局检查：
  - `framework-guide` 其余相关文档已核对，无与当前实现冲突的明显旧口径。

## 2026-04-07 文档同步（智能客服 LangGraph 改造）

- `framework-guide/框架架构与调用链路总览.md`：
  - 将 Chatbot 场景从 `ChatbotChain` 口径更新为 `ChatbotLangGraphRunner` 主链路；
  - 同步补充 `/chatbot/chat/stream`（SSE 主用）与 legacy 回退策略说明；
  - 更新“快速定位链路”小抄为 LangGraph 入口。
- `memory-bank/02-components.md`：
  - 修正 Chatbot 路由为 `/chatbot/chat` + `/chatbot/chat/stream`；
  - 在编排组件中补充 `ChatbotLangGraphRunner` 职责与依赖关系。
- `docs/性能与稳定性评估.md`：
  - 增加 `/chatbot/chat/stream` 作为主压测对象建议，保留 `/chatbot/chat` 兼容口径。
- `docs/Agentic-Workflow-设计蓝图.md`：
  - Chatbot 章节统一改为 LangGraph 口径，标注当前代码已落地图编排。
- 部署文档同步：
  - `deploy-docs/chatbot-deploy.md`：vLLM 启动改为 `vllm-deploy/deploy.sh`，并补充手动 compose 命令；
  - `app/app-deploy/README.md`、`app/app-deploy/README-simple-deploy.md`：vLLM 启动步骤与当前部署脚本对齐。

## 2026-04-10 文档同步（智能客服相似案例 + 4.0 流程图）

- **代码（此前已落地）**：`fault_case_gate`、`chatbot_similar_cases.py`、`ChatRequest.enable_fault_vision`、`CHATBOT_SIMILAR_CASE_*` / `CHATBOT_FAULT_*`（`app/core/config.py`、`app/app-deploy/.env.example`）。
- **`enterprise-level_transformation_docs/企业级智能客服 LangGraph 框架实现方案.md`**：
  - 更新 **§4.0** 业务文字流程（故障域门控、主答后相似案例）、**Mermaid**（`fault_case_gate`、Runner 澄清/流式/二次 RAG 分支、Legacy 说明）、**§4.1 状态**与 **§4.2 节点清单**（含 `fault_case_gate` 与相似案例域字段）；**§1 范围**补充相似案例扩展。
- **`framework-guide/智能客服整体实现技术说明.md`**：主链路步骤、`fault_case_gate`、Runner 相似案例、配置表与文件映射表同步。
- **`framework-guide/RAG整体实现技术说明.md`**：智能客服小节补充限定 namespace 二次检索说明。
- **`docs/工程完成度总览.md`**、**`docs/大小模型应用技术架构与实现方案.md`**（§4.3）、**`docs/Agentic-Workflow-设计蓝图.md`**（§3.2 头注）：与 LangGraph + 相似案例口径对齐。
- **`memory-bank/02-components.md`**：`ChatbotLangGraphRunner` 职责描述更新。

