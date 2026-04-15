# 多步 Agent Workflow 设计蓝图（推理 / 综合分析 / Chatbot / NL2SQL）

> 本文在《大小模型应用技术架构与实现方案.md》的基础上，专门对四个核心业务场景的多步 Agent Workflow 进行蓝图级设计，作为 Agentic RAG 与 LangChain/LangGraph 编排的后续实现参考。  
> 当前代码中已落地 Agentic RAG 基座与部分接入点，本蓝图主要描述「可演进的多步流程」，不强绑定当前版本是否完全实现。

---

## 1. 通用推理 `/llm/infer`（LLMInferenceService）

### 1.1 目标

- 在不破坏现有统一推理网关的前提下，为通用推理增加一个轻量的 Agentic 流程：  
  「问题诊断/规划 →（可选 Agentic RAG）→ 主回答生成」。
- 通过 `rag_mode`（basic / agentic）开关控制是否启用 Agentic 流程，默认行为保持与当前 basic 模式一致。

### 1.2 多步 Workflow 骨架

1. **Step 0：入参解析与基础上下文构建**
   - 从 `LLMInferenceRequest` 中提取：
     - 用户可见内容（`prompt` 或最后一条 user message）；
     - RAG/上下文开关、`rag_mode` 等。
   - 记录用户消息到 `ConversationManager`，准备历史上下文。

2. **Step 1：问题归类与意图分析（仅在 `rag_mode=agentic` 时启用）**
   - 使用 LangChain LLM 做一次短调用（可复用当前 LLM 实例）：
     - 输入：
       - 当前用户问题文本；
       - 最近若干轮对话摘要（可选）。
     - 输出（结构化文本或 JSON）：
       - `type`: `qa_doc` / `chitchat` / `analysis` / `nl2sql_candidate` / `other`；
       - `need_rag`: true | false；
       - （可选）`sub_questions`: 1~3 个子问题；
       - （可选）`notes`: 对后续答复方式的简单建议。
   - 结果仅用于本次请求内部，不直接暴露给调用方，可部分写入 LangSmith metadata。

3. **Step 2：基于分析结果的 Agentic RAG**
   - 如果 `enable_rag == True`：
     - 若 `rag_mode != agentic`：保持当前 **basic** 行为：
       - 使用 `AgenticRAGService` + `RAGMode.BASIC` 对「用户问题文本」执行单步检索。
     - 若 `rag_mode == agentic`：
       - 根据 Step 1 中的 `type/sub_questions` 构造更丰富的检索 query：
         - 例如：将子问题按「子问题 + 原始问题摘要」拼接；
         - 对不同子问题可多次调用 `AgenticRAGService.retrieve(...)`。
       - 将多轮检索结果进行简单合并与去重，形成「Agentic RAG 证据集」。
   - 将最终的 `context_snippets` 记录在响应与 LangSmith trace 中。

4. **Step 3：主回答生成**
   - 仍然复用现有的两种调用路径：
     - LangChain ChatOpenAI；
     - 或 VLLMHttpClient 回退逻辑。
   - 在构造 Prompt/messages 时，额外拼接：
     - 若有 Step 1 结果：插入一条隐藏的 SystemMessage，简要说明问题类型与回答风格建议；
     - RAG 证据集合（basic / agentic 均如此）。

> 说明：  
> - 当前代码中已经通过 `rag_mode` + `AgenticRAGService` 预留了扩展点；  
> - 上述 Step 1/2 的细化实现可逐步引入，不会影响现有 basic 行为。

---

## 2. 综合分析（AnalysisGraphRunner / AnalysisService）

### 2.1 目标

- 将综合分析从“一次大 Prompt”升级为“规划 + 多步检索 + 汇总证据 + 生成报告”的 Agent 工作流。
- 更好地利用多模态引用（图像/视频/GPS/传感器）的信息，并为后续接入真实多模态小模型工具留出接口。

### 2.2 多步 Workflow 骨架（建议主要在 `AnalysisGraphRunner` 中实现）

1. **Step 0：基础上下文与多模态信息整理**
   - 整理 `AnalysisInput`：
     - 文本描述；
     - 多模态 ID（图像/视频/GPS/传感器）数量与类型；
     - 历史会话上下文（通过 `ConversationManager` 获取）。

2. **Step 1：分析任务分解（Planning）**
   - 使用 LangChain LLM + 专门的 Planner Prompt（scene=`analysis_planner`）：
     - 输入：分析需求描述 + 多模态概览 + 部分历史上下文摘要；
     - 输出「分析计划」：
       - 总体分析目标；
       - 若干子任务（如“确定时间范围”、“识别异常趋势”、“交叉验证不同数据源”等）；
       - 每个子任务建议需要的证据类型（文档/图像/传感器等）。

3. **Step 2：按子任务进行多轮 RAG 检索**
   - 遍历子任务，针对每个子任务构造专门的检索查询：
     - 格式示例：`"子任务描述: ...; 原始问题: ..."`
   - 调用 `AgenticRAGService.retrieve(...)`：
     - `scene="analysis_subtask"`；
     - `RAGMode.AGENTIC`（后续可扩展多步）。
   - 收集每个子任务的检索结果，整理为结构化文本：
     - `# 子任务1: ... \n - 证据片段 A\n - 证据片段 B` 等。
   - 对多个子任务的证据做去重与聚合，得到最终「多步证据集合」。

4. **Step 3：多模态特征与外部工具占位（可选后续扩展）**
   - 当前阶段：仅将多模态 ID 数量/类型以 SystemMessage 形式汇总；
   - 后续阶段：可在此处增加小模型工具调用（如图像检测/视频摘要），将结果作为额外证据插入。

5. **Step 4：生成最终综合分析报告**
   - 在最终 LangChain 调用中构造 messages：
     - 系统 Prompt：scene=`analysis`；
     - 分析计划（Step1 的输出文本）；
     - 多步 RAG 证据集合（Step2 的聚合结果）；
     - 多模态数据概览（现有实现已具备）；
     - 用户当前分析需求（HumanMessage）。
   - 得到 `AnalysisResult.summary`（可后续扩展 `details` 为结构化 JSON，如分章节、结论与建议等）。

---

## 3. Chatbot（LangGraph / ChatbotService）

### 3.1 目标

- 在智能客服场景下实现「意图识别 → 策略路由 → 按需多轮检索 → 回答生成」的 Agent Workflow。
- 为后续跨业务路由（如从 Chatbot 跳转到 NL2SQL 查询）预留接口。

### 3.2 多步 Workflow 骨架（建议主要在 `ChatbotLangGraphRunner` 中实现）

> 当前代码已落地 `ChatbotLangGraphRunner`，并默认由 `/chatbot/chat/stream` 进入图编排；
> 已实现 **`fault_case_gate`**（可选）：故障域判定 + 主回答后 **限定 namespace** 相似案例 RAG（`CHATBOT_SIMILAR_CASE_*`，默认关）；Runner 层流式与落库。
> 下述骨架用于持续演进（新增业务意图、工具调用、跨服务路由等）。

1. **Step 0：基础上下文构建**
   - 通过 `ConversationManager` 获取最近若干轮会话；
   - 将历史 user/assistant 消息按 role 区分，构成上下文 messages。

2. **Step 1：意图识别与路由决策**
   - 使用 LangChain LLM + 意图识别 Prompt（scene=`chatbot_router`）：
     - 输入：当前用户问题 + 最近会话摘要；
     - 输出：
       - `intent`: `qa_doc` / `faq` / `chitchat` / `nl2sql_candidate` / `handoff_human` 等；
       - `need_rag`: true/false；
       - （可选）`topic` / `product` 等标签；
       - （可选）是否建议转 NL2SQL 或调用其他业务服务。
   - 将意图信息（特别是 intent / need_rag）记录在本次对话的内部上下文，可写入 LangSmith metadata。

3. **Step 2：基于意图的 RAG 策略选择**
   - 若 `need_rag == False`：可直接跳过 RAG，进入 Step 3。
   - 若 `need_rag == True`：
     - 构造一条增强后的检索 query：
       - 例如：`"[意图=faq, 主题=产品配置] + 原始用户问题"`；
     - 调用 `AgenticRAGService.retrieve(...)`：
       - `scene="chatbot"`；
       - `RAGMode.AGENTIC`（后续可实现：先 FAQ 库检索，再文档库补充）。
   - 获得 RAG 结果后，将关键片段整理为 SystemMessage，指示模型「优先参考以下知识片段进行回答」。

4. **Step 3：主回复生成**
   - 构造 LangChain messages：
     - 系统 Prompt：scene=`chatbot`；
     - 一条隐藏的 SystemMessage：描述本轮意图与回答风格（例如 FAQ 回答简明、给链接；chitchat 更自然友好等）；
     - 历史上下文 messages（已有逻辑）；
     - RAG 片段 SystemMessage（如有）；
     - 当前用户问题 HumanMessage。
   - 调用 LangChain LLM 生成最终回答；仍通过 `ChatbotService` 对外暴露统一接口。

5. **Step 4：后续扩展（可选）**
   - 若 intent 为 `nl2sql_candidate`，可在后续版本中：
     - 将当前问题与意图信息转交 NL2SQL Service；
     - 或返回一个“需要进一步澄清”的问题给用户。

---

## 4. NL2SQL（NL2SQLChain / NL2SQLService）

### 4.1 目标

- 将 NL2SQL 从「单步 SQL 生成」升级为「问题理解 → RAG 精细检索 → SQL 初稿 → 自检/修正」的多步链路。
- 更好利用 `NL2SQLRAGService` 的多命名空间（schema / biz / qa），并将 SQLValidator 纳入 Agentic 自检闭环。

### 4.2 多步 Workflow 骨架（建议主要在 `NL2SQLChain` 中实现）

1. **Step 0：基础信息与 Schema 上下文准备**
   - 通过 `SchemaMetadataService` 获取当前已知表/字段摘要，用作规划参考；
   - 针对问题和已加载的 Schema 做简单关键词匹配（可选）。

2. **Step 1：问题理解与实体/表识别（Planning）**
   - 如 LangChain 可用：
     - 使用 `_lc_chat_model` + 规划 Prompt（scene=`nl2sql_planner`）：
       - 输入：自然语言问题 + Schema 概览（压缩版）；
       - 输出：
         - 可能涉及的业务实体/表名（文本建议）；
         - 需要的关键字段（时间/状态/主键等）；
         - 是否需要 join、多表查询、聚合等。
   - 该规划结果不会直接影响 SQL 校验，但用于指导后续 RAG 检索与 Prompt 构造。

3. **Step 2：基于规划结果的多命名空间 RAG 检索**
   - 在 `NL2SQLRAGService` 的基础上：
     - 对 Schema 命名空间：带上候选表名进行检索；
     - 对业务知识命名空间：检索与问题语义/业务规则相关的片段；
     - 对 Q&A 命名空间：检索类似问答示例。
   - 最终组合为 `schema_snippets`，用于 PromptBuilder。

4. **Step 3：生成 SQL 初稿**
   - 使用 `PromptBuilder` 构建 NL2SQL prompt：
     - System 部分包含：
       - 规划结果摘要（Step1）；
       - 关键 Schema / 业务规则片段（Step2）；
     - User 部分为原始自然语言问题。
   - 通过 LangChain ChatOpenAI（如可用）或 VLLMHttpClient 生成 SQL 初稿。

5. **Step 4：SQL 自检与修正**
   - 使用 `SQLValidator` 进行安全/只读校验：
     - 若通过：直接返回；
     - 若不通过，且 LangChain 可用：
       - 再发起一轮 LLM 调用（scene=`nl2sql_refine`）：
         - 输入：原问题 + 初稿 SQL + 校验错误信息 + 部分 Schema 片段；
         - 要求模型「输出修正后的、安全的 SQL」。
       - 再次使用 `SQLValidator` 校验；
       - 若仍不通过，则返回空字符串，并在日志中记录原因。

---

## 5. 实施策略与兼容性说明

- **增量落地**：上述多步 Workflow 都可以分步引入（先实现 Planner，再引入多轮 RAG，再补 SQL 自检等），每一步都保证对现有调用兼容。
- **依赖可选**：所有多步逻辑均应在 LangChain 可用时启用，不可用时保持当前单步逻辑，防止运行环境不完整导致主功能不可用。
- **公共能力沉淀**：
  - 若多个场景中出现高度相似的子流程（如通用 Planner / Clarification Step），可以将其提炼成 `agentic.py` 中的通用工具或小型 Workflow 组件；
  - 但顶层业务剧本（具体步骤顺序与场景语义）仍建议保留在对应的 Chain/Service 内。

---

## 6. 当前实现成熟度与后续提升方向

> 本节用于说明：当前代码已经实现的 Agentic RAG 能力处于什么等级，以及若要演进为“生产级企业 Agentic RAG 系统”，有哪些可进一步提升的方向。

### 6.1 当前成熟度定位

- **架构 / 代码层**：
  - 已具备清晰分层与抽象：
    - RAG 基座：`AgenticRAGService` + `RAGMode` + `RAGContext`，统一处理多场景 RAG 调用；
    - 业务多步链路：`LLMInferenceService` / `AnalysisGraphRunner` / `ChatbotLangGraphRunner` / `NL2SQLChain` 各自实现轻量多步 Agent Workflow（Planner / Intent / RAG / Refine）；
    - 降级策略：LangChain 不可用时自动回退到单步逻辑。
  - 可以视为**“企业级 Agentic RAG 的工程基座 + 多步骨架实现”**，后续增强可以在此基础上增量演进，而无需推翻现有设计。

- **运行策略 / 运营侧**：
  - 更接近 **POC+/Pilot 等级**：
    - 多步流程已经落地，但策略和开关尚未完全配置化；
    - 尚未形成完整的 A/B 实验与效果评估闭环。

### 6.2 与生产级企业 Agentic RAG 的主要差距

1. **策略与配置尚未完全外置**
   - 当前：
     - Planner / Intent 的 Prompt 与行为逻辑主要写在代码中；
     - 是否启用 Agentic 流程主要由简单开关（如 `rag_mode` / `enable_rag`）控制。
   - 生产级期望：
     - 通过配置中心（DB/YAML/FeatureFlag 等）管理：
       - 各场景的 Planner Prompt / Intent Prompt 与参数；
       - agentic/basic 流程的灰度比例与按环境/租户/用户维度的差异化开关。

2. **缺少显式图式编排（如 LangGraph 状态机）**
   - 当前：
     - 多步 Workflow 以“顺序函数调用”的方式编码（例如 Planner → RAG → 主调用），逻辑清晰但流程结构隐式；
   - 生产级期望：
     - 采用 LangGraph 或自研 Workflow Engine，将关键场景（尤其是综合分析与 NL2SQL）的流程建模为有向图：
       - 显式描述节点（步骤）与边（条件/跳转）；
       - 支持节点级重试、超时、回滚和可视化调试。

3. **鲁棒性与异常处理策略仍属基础版**
   - 当前：
     - 多步逻辑主要通过 `try/except` + 日志 + 回退到 basic 流程实现容错；
   - 生产级期望：
     - 为不同错误类型（Planner 超时、RAG 超时、LLM 抛错等）定义更精细的策略：
       - 不同节点的重试策略与最大重试次数；
       - 节点级熔断与降级（例如 Planner 持续失败则关闭 agentic 流程）；
       - 关键错误的可观测标签与告警规则。

4. **评估与 A/B 实验闭环尚未系统化**
   - 当前：
     - 已有 LangSmithTracker 与 Prometheus 指标，但尚未形成“basic vs agentic”的对照实验方案；
   - 生产级期望：
     - 设计并实施完整的评估闭环：
       - 线下评估集（典型问题 + 标准答案/评分标准）；
       - 在线指标（成功率、错误率、转人工率、用户反馈等）；
       - 实验组/对照组（basic 组 vs agentic 组）的分流与结果对比；
       - 评估结果沉淀到专门的效果评估文档中。

5. **自动化测试与 Mock 测试覆盖有待加强**
   - 当前：
     - 以骨架实现为主，尚未附带系统性的单元测试/集成测试；
   - 生产级期望：
     - 引入 Mock LLM / Mock RAG 组件，对多步流程进行可重复的自动化测试：
       - 测试 Planner/Intent 输出为特定模式时的下游行为；
       - 测试 Planner/RAG/LLM 抛错时的降级与回退路径；
     - 为关键业务场景构建回归测试集，并纳入 CI/CD。

### 6.3 后续可提升的方向建议

1. **短期（易落地）**
   - 将 Planner / Intent / Refine 的 Prompt 与启用策略抽象为配置（可先用 YAML + `PromptTemplateRegistry` 或新增配置模块加载）；
   - 在 LangSmith trace 与日志中增加 Agentic 相关 metadata 字段（如 `agentic_flow_version`、`intent_type`、`planner_success` 等），便于后续分析。

2. **中期（增强可观测性与安全性）**
   - 为每个 Agentic 步骤增加更细粒度的 Prometheus 指标与日志打点：
     - Planner 成功率/失败率/平均耗时；
     - Agentic 流程触发率与回退率；
     - NL2SQL 中初稿 SQL 与修正 SQL 的校验通过率。
   - 在 NL2SQL 与综合分析场景中，补充一批典型问题的自动化回归测试。

3. **长期（全面生产化）**
   - 引入 LangGraph 或轻量级 Workflow Engine，将综合分析与 NL2SQL 场景的多步 Workflow 显式建模为图结构；
   - 设计完整的 A/B 实验方案（basic vs agentic），并在 `docs/` 下新增效果评估文档，记录上线策略、评估结果与迭代结论；
   - 将 Agentic 策略与配置纳入统一配置中心/运维平台管理，实现跨环境一致的策略下发与审计。

---

> 本蓝图文件与《大小模型应用技术架构与实现方案.md》共同构成 Agentic RAG 与多步 Workflow 的设计依据。实际实现进度与差异请以 `memory-bank/05-progress-log.md` 中的时间序列记录为准。

