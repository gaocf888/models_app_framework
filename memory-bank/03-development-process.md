# 开发流程与规范（大小模型统一基座）

## 1. 代码结构与模块划分（建议）

> 目录仅为建议，实际落地时可微调，但需保持「按功能域拆分 + 接口清晰」这一原则，并且**完整覆盖首个提示词中的所有能力需求**。

- `app/`
  - `api/`：FastAPI 路由层（所有对外/对内 HTTP 接口的入口）
    - `llm_inference.py`：大模型推理接口（纯推理，带 RAG/上下文开关参数）
    - `chatbot.py`：智能客服接口（支持流式响应、会话管理）
    - `analysis.py`：综合分析 Agent 接口（Agentic RAG，多模态输入占位）
    - `nl2sql.py`：NL2SQL 查询与管理接口（自然语言查询 + Schema 刷新/测试等）
    - `small_model.py`：小模型通道管理接口（start/stop/update/status）
    - `rag_admin.py`：RAG 知识摄入与管理接口（文档/Schema/业务知识导入、索引重建等）
    - `train_admin.py`：训练/微调管理接口（大模型/小模型训练任务查询与控制，主要内部/运维使用）
    - `healthcheck.py`：健康检查与基础信息
  - `services/`：业务无关服务层（大模型/小模型/RAG/NL2SQL/训练）
    - `llm_inference_service.py`：封装大模型通用推理逻辑（接入 LLMClient、RAG、上下文等）
    - `chatbot_service.py`：封装智能客服会话逻辑（多轮对话、RAG、用户上下文）
    - `analysis_service.py`：封装综合分析 Agent 调用与多模态分析流程
    - `nl2sql_service.py`：封装 NL2SQL 端到端流程（问题 → RAG → SQL 生成 → 校验 → 执行）
    - `small_model_channel_service.py`：封装小模型通道管理对外服务（调用 ChannelManager）
    - `rag_ingestion_service.py`：RAG 知识摄入业务逻辑
    - `training_service.py`：统一的训练任务管理（封装 LLaMA-Factory + 小模型训练）
  - `models/`：Pydantic 请求/响应模型
    - 按子域拆分：`llm.py`、`chatbot.py`、`analysis.py`、`nl2sql.py`、`small_model.py`、`rag.py`、`train.py` 等
  - `core/`：通用核心能力
    - `config.py`：全局配置加载（模型、RAG、日志、A/B 策略等）
    - `logging.py`：`LoggingManager` 封装
    - `metrics.py`：Prometheus 指标注册与导出
    - `tracing.py`（可选）
    - `errors.py`：统一异常与错误码定义
  - `llm/`：大模型与 LangChain 相关
    - `client.py`：LLMClient 抽象与具体实现（vLLM/云端模型调用封装）
    - `config_registry.py`：LLMConfigRegistry，实现大模型配置注册与查询
    - `chains/`
      - `chatbot_chain.py`：基于 LangChain 定义的智能客服链路
      - `analysis_chain.py`：基于 LangChain/Agent 定义的综合分析链路
      - `nl2sql_chain.py`：基于 LangChain 定义的 NL2SQL 链路（调用 NL2SQL 组件）
    - `prompts/`：按场景划分 Prompt 模板
    - `prompt_registry.py`：PromptTemplateRegistry 实现，支持版本管理与 A/B 策略
  - `rag/`
    - `vector_store.py`：向量库适配层（FAISS/Milvus/pgvector/ES 等）
    - `embedding_service.py`：嵌入服务封装（统一调用文本/多模态嵌入模型）
    - `rag_service.py`：RAG 检索与上下文构建服务（传统 RAG + Agentic RAG 工具）
    - `ingestion.py`：RAGIngestionService 具体实现（分片、嵌入、写入向量库等）
  - `conversation/`
    - `store.py`：基于 Redis 的会话存储封装（读取/写入会话记录与摘要）
    - `manager.py`：ConversationManager，实现多轮对话上下文管理策略
  - `small_models/`
    - `channel_manager.py`：ChannelManager 实现，管理通道生命周期与线程资源
    - `workers.py`：解码/推理等工作线程实现（DecoderWorker、InferenceWorker 等）
    - `inference_engine.py`：小模型推理引擎封装（YOLO/Seg/Cls 等算法统一接口）
    - `registry.py`：SmallModelRegistry
    - `training.py`：SmallModelTrainingService（集成 TensorBoard）
  - `nl2sql/`
    - `schema_service.py`：数据库 Schema 元数据管理与同步
    - `rag_service.py`：NL2SQL 专用 RAG 服务（Schema/业务知识/问答样例检索）
    - `prompt_builder.py`：NL2SQL Prompt 构建器（将 Schema/RAG 结果拼装为提示词）
    - `chain.py`：NL2SQL 链 orchestration（调用 LLM 生成 SQL，并触发自我修正）
    - `validator.py`：SQLValidator，实现 SQL 语法/安全校验与策略约束
    - `executor.py`：SQLExecutor，多数据源适配与 SQL 实际执行
  - `monitoring/`
    - `prometheus.py`：应用级指标（API、大模型、小模型、RAG、NL2SQL）
    - `loki.py`（日志配置与 Loki 集成）
    - 说明：数据库、系统与容器监控由基础设施（Exporter + Prometheus + Grafana）统一采集，本工程负责暴露应用级指标与结构化日志。
  - `train/`（训练逻辑）
    - `llm_factory_adapter.py`：与 LLaMA-Factory 的集成适配层（大语言模型和多模态大模型的任务配置/启动/状态查询）
    - `llm_training.py`：大模型代码训练/微调脚本封装（LoRA/全参等，覆盖大语言模型与多模态模型）
    - `small_model_training.py`：小模型训练脚本封装（数据加载、训练循环、评估，与 `small_models/training.py` 中的 TensorBoard 可视化配合）
  - `main.py`：FastAPI 入口

## 2. 开发流程（从需求到上线）

1. **需求/能力确认**
   - 明确本次改动属于哪个能力域（大模型推理/小模型通道/智能客服/综合分析/NL2SQL/运维等）。
   - 在 `memory-bank` 文档中确认是否已有约束；如有差异需先更新架构文档。

2. **设计与评审**
   - 在 `01-architecture.md` / `02-components.md` 上补充或更新相关设计；
   - 对涉及多线程、共享状态或新模型部署的改动，重点评审线程安全与资源管理。

3. **实现**
   - 遵循模块划分，在对应目录中新增/修改代码；
   - 避免将业务逻辑写入 API 层，保持 API 层薄，业务逻辑在服务层或链路层。

4. **测试**
   - 单元测试：对核心组件（ChannelManager、RAGService、NL2SQLChain 等）编写单测；
   - 集成测试：覆盖典型请求流程；
   - 并发/压力测试：特别是大模型高并发与小模型多通道场景。

5. **可观测性与回滚策略**
   - 为新能力埋点监控指标（Prometheus）和关键日志；
   - 定义上线后回滚条件与操作方式（版本回退/流量切换等）。

6. **上线与验证**
   - 预发布环境验证：包括负载、并发、异常场景；
   - 正式环境灰度：逐步提高流量占比；
   - 收集反馈与指标，评估是否进入稳定状态。

## 3. 代码风格与通用规范

- Python 版本：3.10+。
- 代码风格：PEP 8，结合内部约定：
  - 类型注解必须齐全（输入输出与重要中间变量）。
  - 使用 Pydantic 定义 API 层输入输出模型。
  - 避免在函数内定义大块嵌套函数/类，降低认知负担。

- 错误处理：
  - 服务层将可预期业务错误转换为统一的异常类型，API 层统一转 HTTP 响应码。
  - 不在底层组件中吞掉异常，至少需打日志并重新抛出或转译。

- 日志：
  - 严禁在核心路径使用 `print`；
  - 使用统一 `LoggingManager` 获取 logger；
  - 日志需带上 `trace_id` / `user_id` / `session_id`（通过上下文或中间件注入）。

## 4. 并发与线程安全规范

- **小模型相关**
  - 所有对通道对象映射的访问必须在 `_objects_lock` 下进行；
  - 对单通道的启动/停止/更新逻辑在 `channel_lock` 下串行；
  - 严禁使用 `queue.empty()` / `qsize()` 作业务判断或收尾；
  - 长耗时操作或 IO 不得在持有全局锁时执行。

- **大模型相关**
  - LangChain 链实例：
    - 不在多个线程间共享带有可变内部状态的实例；
    - 更推荐「配置不可变 + 每请求/每 worker 构建轻量实例」。
  - LLM Client：
    - HTTP client 对象（如 `httpx.AsyncClient`）可按进程或 worker 维度共享；
    - 需要明确连接池与重试策略。

- **会话/缓存**
  - 使用 Redis 时，对涉及「读后写」场景需考虑乐观锁或 Lua 脚本（例如并发更新同一会话摘要）。

## 5. 文档与 memory-bank 维护规范

- 所有影响架构、组件关系或对外能力的变更，必须同步：
  - `01-architecture.md`：更新整体架构或关键设计决策；
  - `02-components.md`：新增/修改组件说明；
  - `03-development-process.md`（当前文件）：如有流程变化；
  - `04-progress.md`、`05-progress-log.md`：记录里程碑与时间序列变更。

- 避免创建过多零散文档：
  - 与本基座相关的高层说明尽量归并到 `docs/` 与 `memory-bank/` 内；
  - 详细设计可以在代码注释与 README 中补充，但需在这些核心文档中留有索引。

## 6. 测试与质量保障

- 单元测试：
  - 对复杂逻辑（队列收尾、NL2SQL 自我修正、RAG 检索策略）必须有单测；
  - 尽量使用 pytest + fixtures。

- 集成测试：
  - 构造模拟 vLLM/小模型/向量库/数据库环境；
  - 覆盖关键路径：
    - Chatbot RAG 问答；
    - 综合分析多模态输入；
    - NL2SQL 生成 + 执行；
    - 小模型多通道推理。

- 性能与压力测试：
  - 针对大模型接口，使用压测工具（locust/jmeter 等）验证并发性能；
  - 针对小模型通道，验证在多路视频/高帧率下的稳定性与资源利用率。

## 7. 部署与运行（简要约定）

- 部署方式建议：
  - 容器化（Docker/K8s），与 Prometheus/Grafana/Loki Sidecar / Agent 配合；
  - 以环境变量/配置文件形式注入模型路径、vLLM 地址、向量库地址等。

- 运行时约定：
  - 必须暴露：
    - `/health` 健康检查；
    - `/metrics` 指标（Prometheus）。
  - 日志输出到 stdout/stderr + 按天轮转的文件（供 Loki 收集）。

