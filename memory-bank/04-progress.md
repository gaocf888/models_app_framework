# 进度概览（里程碑级）

> 本文件记录架构基座层面的阶段性进度与里程碑，用于高层跟踪；具体实现细节与时间序列变更见 `05-progress-log.md`。

## 阶段 0：基线文档与目标（当前）

- ✅ 完成
  - 明确项目目标与范围（大小模型统一技术基座）。
  - 基于《高并发多线程安全应用架构设计》确定大小模型在架构与并发模型上的区分方案。
  - 基于《NL2SQL系统概要设计》确定 NL2SQL 在整体架构中的位置与集成方式。
  - 建立 `memory-bank/` 目录与核心文档骨架（00~06）。

- 待办（后续阶段）
  - 细化具体工程目录结构与初始化代码框架。
  - 为大模型推理、智能客服、综合分析、NL2SQL、小模型通道分别定义首版接口与数据模型。

## 阶段 1：工程骨架与基础设施

- 目标
  - 建立 FastAPI 应用骨架，按 `03-development-process.md` 中建议目录组织代码。
  - 接入统一日志与监控（Logging + Prometheus + Loki 基础配置）。
  - 搭建 vLLM 开发环境与简单的 LLM 推理 Demo（不含复杂 RAG）。
  - 引入 Redis、向量库与数据库连接基础封装。

- 交付物
  - 可启动的服务（基础健康检查 + `/metrics` 指标端点）。
  - 初版 README 与部署说明（后续补充）。

- 当前进度
  - 已创建基础工程骨架：
    - `app/main.py`：FastAPI 应用入口与 `/metrics` 暴露。
    - `app/core/logging.py`：全局日志初始化（基于统一配置中心）。
    - `app/core/metrics.py`：Prometheus 指标定义。
    - `app/api/healthcheck.py`：基础健康检查接口。
    - `app/core/config.py`：统一配置中心初始实现（LLM/RAG/日志/Prompt A/B 等配置结构）。
    - `app/rag/vector_store.py`：向量库抽象与内存占位实现。
    - `app/rag/embedding_service.py`：嵌入服务占位实现。
    - `app/rag/rag_service.py`：RAGService 最小检索能力实现（使用默认向量库与配置的 top_k）。
    - `app/conversation/store.py`：会话存储内存占位实现。
    - `app/conversation/manager.py`：ConversationManager 会话管理器基础实现。
    - `app/models/chatbot.py`：智能客服请求/响应数据模型。
    - `app/services/chatbot_service.py`：ChatbotService 基础服务（占位回答，已集成 RAG 与会话管理）。
    - `app/api/chatbot.py`：`/chatbot/chat` 接口，打通智能客服基础链路 V1。
    - `app/models/analysis.py`：综合分析请求/结果数据模型。
    - `app/services/analysis_service.py`：AnalysisService 基础服务，占位实现 Agentic RAG + 多模态分析流程。
    - `app/api/analysis.py`：V2 双入口（`/analysis/run-with-payload`、`/analysis/run-with-nl2sql`），完成企业版链路收敛。
    - `app/small_models/channel_manager.py`：小模型通道管理器骨架，实现通道的 start/stop/update/status。
    - `app/small_models/workers.py`：Decoder/Inference 工作线程占位实现。
    - `app/small_models/inference_engine.py`：小模型推理引擎占位实现。
    - `app/models/small_model.py`：小模型通道配置与状态的数据模型。
    - `app/services/small_model_channel_service.py`：通道管理服务，对外暴露简化接口。
    - `app/api/small_model.py`：`/small-model/channel/*` 接口，打通小模型通道管理基础链路 V1。
    - `app/small_models/registry.py`：小模型注册表骨架，用于管理可用小模型及其元数据。
    - `app/small_models/training.py`：小模型训练服务骨架，支持以配置启动训练任务，后续集成 TensorBoard。
    - `configs/small_models.yaml`：小模型算法配置示例文件（算法类型、权重路径、阈值等），支持安全帽/打电话等任务的配置化管理。
    - `app/nl2sql/schema_service.py`：NL2SQL Schema 元数据服务骨架。
    - `app/nl2sql/rag_service.py`：NL2SQL 专用 RAG 封装。
    - `app/nl2sql/prompt_builder.py`：NL2SQL Prompt 构建器。
    - `app/nl2sql/validator.py`：SQLValidator，限制为只读 SELECT 语句。
    - `app/nl2sql/executor.py`：SQLExecutor 骨架（后续接入真实数据库）。
    - `app/nl2sql/chain.py`：NL2SQLChain，负责 RAG + Prompt + LLM + 校验。
    - `app/models/nl2sql.py`：NL2SQL 查询请求/响应模型。
    - `app/services/nl2sql_service.py`：NL2SQLService，封装链路与会话记录。
    - `app/api/nl2sql.py`：`/nl2sql/query` 接口，打通 NL2SQL 基础链路 V1。
    - `configs/prompts.yaml`：提示词与 A/B 策略示例配置文件，支持 chatbot/analysis/nl2sql 场景多版本模板。
    - `app/llm/prompt_registry.py`：PromptTemplateRegistry，实现从配置加载模板并按用户 ID 做哈希分流的 A/B 测试逻辑。
    - `app/train/llm_factory_adapter.py`：LLaMA-Factory 适配层骨架，定义训练配置并预留可视化训练/微调入口。
    - `app/train/llm_training.py`：LLMTrainingService 骨架，定义代码方式的大模型训练/微调配置与启动入口。
    - `app/core/metrics.py`：扩展 LLM/RAG/小模型/NL2SQL 维度的 Prometheus 指标（请求计数与耗时等），支撑 TODO-13 的监控落地。

## 阶段 2：大模型能力（推理/智能客服/综合分析）

- 目标
  - 实现通用 LLM 推理服务（支持 RAG/上下文可配置）。
  - 实现基础智能客服链路（文本问答 + RAG + Redis 会话）。
  - 实现首版综合分析（后续已演进为 LangGraph 企业版 V2，支持双入口、RAG 证据与节点轨迹）。
  - 完成 Prompt 模板管理与简单版本控制机制。

- 交付物
  - 文档更新：架构与组件说明补充。
  - 可通过 API 调用的智能客服与分析能力 Demo。

## 阶段 3：小模型通道与训练封装

- 目标
  - 实现小模型通道管理、解码与推理多线程流水线（参考 `ym_new`）。
  - 引入多算法配置化与通道级参数管理。
  - 集成 TensorBoard 或其他可视化工具用于训练监控。

- 交付物
  - 文档更新：小模型架构与线程安全方案的落地说明。
  - 多路视频通道 Demo 与训练脚本示例。

## 阶段 4：NL2SQL 能力落地

- 目标
  - 将 NL2SQL 智能层与数据访问层集成进现有基座。
  - 打通端到端链路：自然语言 → SQL → 执行 → 结果解释。
  - 完成必要的安全与监控埋点。

- 交付物
  - 可运行的 NL2SQL API；
  - 相关评估与回归测试脚本。

## 阶段 5：性能优化与 A/B 测试体系

- 目标
  - 针对大模型高并发场景与小模型多通道场景进行系统性压测与优化。
  - 引入 LangSmith 与内部 A/B 测试机制，对 Prompt/模型/策略进行持续迭代。

- 交付物
  - 性能压测报告；
  - A/B 实验记录与效果分析。

