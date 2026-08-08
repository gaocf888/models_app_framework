# 智能客服 LangGraph 收敛与 Hybrid 意图改造方案（简要）

> **状态**：方案稿（待排期实施）  
> **范围**：**仅**流式 `POST /chatbot/chat/stream`（及配套 upload/stop/sessions）；对齐现网 `dev_djs` / `ChatbotLangGraphRunner`  
> **对照**：`enterprise-level_transformation_docs/企业级智能客服 LangGraph 框架实现方案.md`  
> **目标**：① 去掉 Legacy / 非流式 / 一切非图兜底，**仅** `StateGraph` + SSE；② 图后非流式步骤收编进图；③ 新增 RAG+NL2SQL 综合意图  
> **前置约定**：
> - 运行环境 **必选安装 `langgraph`**；**不保留**无包时顺序 `_node_*` 回退  
> - **删除** `CHATBOT_GRAPH_ENABLED`  
> - **智能客服只保留流式接口与链路**；删除非流式 `POST /chatbot/chat` 及对应 Service/`ChatbotChain` 逻辑

---

## 1. 背景与结论摘要

| # | 改造项 | 结论 | 一句话 |
|---|--------|------|--------|
| P1 | Graph-only + **Stream-only** | **做** | 删 Legacy、顺序回退、`CHATBOT_GRAPH_ENABLED`、非流式 `/chat` 与 `ChatbotChain`；唯一对话入口为 SSE |
| P2 | Runner 图后逻辑进 StateGraph | **部分做** | 案例/关联问等进图；**SSE 流式吐字仍留 Runner** |
| P3 | 新增 RAG+NL2SQL 综合意图 | **做** | 互斥三分流无法覆盖「既要数又要机理」；新标签 + 双臂合成 |

**建议实施顺序**：P1（硬切 Graph-only + Stream-only）→ P2 → P3。

---

## 2. P1：移除 Legacy / 顺序回退 / 图开关 / 非流式，只保留 StateGraph + SSE

### 2.1 现状（待删除）

| 旁路 | 触发 / 入口 | P1 处置 |
|------|-------------|---------|
| Legacy 流式 | `CHATBOT_GRAPH_ENABLED=false` | **删除** `_stream_chat_legacy_events` 及关图分支 |
| Legacy 异常回退 | `CHATBOT_FALLBACK_LEGACY_ON_ERROR=true` | **删除** fallback 调用与配置 |
| 顺序 `_node_*` 合并 | `langgraph` 不可用、`_build_graph()` 返回 `None` | **删除**；构建失败则**启动/请求直接失败** |
| 图开关配置 | `CHATBOT_GRAPH_ENABLED` | **删除** |
| **非流式对话** | `POST /chatbot/chat`（已 deprecated） | **删除** API 路由与 OpenAPI 残留说明 |
| **非流式 Service** | `ChatbotService.chat` | **删除** |
| **ChatbotChain** | 非流式路径可选 LangChain 编排 | **删除**对客服主链路的引用；`app/llm/chains/chatbot_chain.py` 若无其它引用则移除或移出客服范围 |

保留（仍属流式配套，非「非流式对话」）：

- `POST /chatbot/upload`、`POST /chatbot/chat/stop`
- `GET/PATCH/DELETE /chatbot/sessions*`（会话读写，不产出整轮非流式回答）

### 2.2 目标形态

```text
【唯一对话入口】POST /chatbot/chat/stream
  → app/api/chatbot.py · chat_stream（SSE）
  → ChatbotService.stream_chat_events
       → 预处理 / Outline / stream_id
       → ChatbotLangGraphRunner.run_stream_events
            → 必选 StateGraph.ainvoke（无图则报错）
            → 图后 SSE adapter
  → 无 /chat；无 Legacy；无 CHATBOT_GRAPH_ENABLED；无 ChatbotChain
```

依赖约定：

- `langgraph` 为**硬依赖**。
- `_build_graph()` 失败或 `self._graph is None`：明确报错，**无**顺序合并兜底。

### 2.3 步骤

| 阶段 | 动作 | 说明 |
|------|------|------|
| P1-a | `langgraph` 必装；Runner 初始化要求图编译成功 | CI/镜像缺包即失败 |
| P1-b | 删除 `CHATBOT_GRAPH_ENABLED` 及所有读写分支 | — |
| P1-c | 删除 `_stream_chat_legacy_events`、`FALLBACK_LEGACY_ON_ERROR` | oncall 改为发版/细开关 |
| P1-d | 删除 `_run_graph` 中「`_graph is None` → 顺序 `_node_*`」 | 仅 `ainvoke` |
| P1-e | **删除非流式全链路** | 见下表 |

**P1-e 非流式删除清单**

| 位置 | 动作 |
|------|------|
| `app/api/chatbot.py` | 删除 `POST /chat`（`chat` handler）；文档字符串改为「仅流式」 |
| `app/services/chatbot_service.py` | 删除 `chat()`；去掉 `_chain` / `ChatbotChain` 初始化与调用 |
| `app/llm/chains/chatbot_chain.py` | 确认无引用后删除，或移出客服并改文档（禁止再被客服引用） |
| `app/models/chatbot.py` 等 | 若 `ChatResponse` **仅**服务于非流式，评估删除或保留给其它只读场景；以无死代码为准 |
| 测试 / Gradio / 部署文档 | 凡调用 `/chatbot/chat` 的改为 `/chat/stream` 或删除 |
| 企业级方案 §10「非流式下线节奏」 | 改为「已决定直接删除，无兼容窗口」 |

调用方迁移：公告「仅支持 SSE `/chatbot/chat/stream`」；非流式调用将得到 **404/410**（实施时二选一，建议 **404** 或显式 **410 Gone**）。

### 2.4 风险与验收

- **风险**：仍依赖非流式 `/chat` 的外部系统会中断；缺包/图编译失败即不可用（部署问题）。
- **验收**：
  - 对话入口仅 `/chat/stream`；`/chat` 不存在或返回 Gone。
  - 不存在 `CHATBOT_GRAPH_ENABLED`、Legacy、顺序回退、`ChatbotService.chat`、客服路径上的 `ChatbotChain`。
  - 流式冒烟：意图三分支 + stop + sessions 正常。
  - 屏蔽 langgraph：**快速失败**，不静默降级。

---

## 3. P2：图后逻辑收编（完整编排 vs 流式边界）

### 3.1 原则

- **进图**：路由、检索、查数准备、相似案例、关联问、（可选）落库计划。
- **留 Runner（streaming adapter）**：`VLLMHttpClient.stream_chat`、`citation_ref` 拆流、NL2SQL 分析的 **SSE yield**、cancel / partial / latency budget。

### 3.2 建议迁入 StateGraph 的节点

| 现 Runner 步骤 | 建议 | 节点示意 |
|----------------|------|----------|
| `_maybe_similar_cases_extra` | 进图 | `similar_cases_retrieve`（条件边：非 A/B 且门控命中） |
| `_fill_suggested_questions` | 进图 | `suggest_followups`（B/`data_query`、未来 hybrid 策略可配） |
| `_persist_*` | 谨慎 | 优先「图内写好 content，图外一次 append」；或 `persist` 节点 + 幂等键防重入 |
| NL2SQL `stream_plan` 生成 | 已在图内 | 保持；**yield 仍在图后** |
| 主答 token 流 / citation | **不进普通节点** | 继续 `run_stream_events` |

### 3.3 目标图结构（简图）

```text
… → finalize_prep
      → [optional] similar_cases_retrieve
      → [optional] suggest_followups
      → END
Runner: stream_or_emit(state) → persist_once → finished.meta
```

### 3.4 验收

- LangSmith / `graph_nodes` 能看见案例与关联问节点耗时。
- 流式协议不变；cancel / partial 行为不回退。
- 图重跑不导致重复 user/assistant 落库。

---

## 4. P3：新增 Hybrid 意图（RAG + NL2SQL 综合）

### 4.1 动机

当前 `clarify` / `data_query` / `kb_qa` **互斥**；规则对混合问只能 `mixed_prefers_*` 二选一。用户常见诉求：「查出超温点 + 结合规程解释/处置」需要**两路证据再综合**。

### 4.2 新意图

- 标签建议：`hybrid_qa`（或 `rag_nl2sql`）
- 纳入 `CHATBOT_INTENT_OUTPUT_LABELS`（灰度：先测试环境加标签）
- `rules` / `llm` / `bert` 三后端均需可产出；规则优先识别「台账/列表/统计 + 原因/标准/怎么处理」类共现

### 4.3 编排（LangGraph）

```text
_route_by_intent → hybrid_qa
  → fan-out（并行优先）
       ├ arm_nl2sql：复用 nl2sql_answer（可 defer stream_plan / 只要 rows+sql meta）
       └ arm_rag：复用 rag_scope → kb_retrieve →（简化 C-RAG）→ 片段与 citations
  → join 屏障
  → hybrid_synthesize：组装双源 context → LLM（流式由 Runner 推）
  → finalize_prep →（P2）案例/关联问策略
```

**降级**：单臂失败 → 降为纯 `kb_qa` 或纯 `data_query` 语义，并在 `meta` 标明 `hybrid_degraded`。

### 4.4 输出与产品边界

- `meta`：`used_rag=true` 且 `used_nl2sql=true`（降级时按实际）；保留 `rag_citations`、`nl2sql_sql` / `nl2sql_analysis`
- 关联问：默认可下发（与纯 B 区分）；相似案例：同 C，按门控
- **与综合分析分工**：客服 Hybrid = 短答一问一综合；长报告/多槽仍走 `/analysis/*`

### 4.5 延迟与护栏

- 并行两臂，预算纳入 `MAX_GRAPH_LATENCY_MS`；单臂超时可降级
- 综合 prompt 约束：数值以 SQL 结果为准，机理以 RAG 为准，禁止臆造表数据

### 4.6 验收（最小）

1. 纯列表问 → 仍 `data_query`；纯概念问 → 仍 `kb_qa`；混合样例 → `hybrid_qa`
2. 双臂成功：回答同时引用数表结论与文档依据；SSE 可含 `citation_ref`
3. SQL 失败 / RAG 空：可降级且不 5xx
4. P95 延迟可接受或有超时降级日志

---

## 5. 配置与文档同步（实施时）

| 项 | 动作 |
|----|------|
| `CHATBOT_GRAPH_ENABLED` | **删除** |
| `CHATBOT_FALLBACK_LEGACY_ON_ERROR` | **删除** |
| Legacy / 顺序 `_node_*` 回退 | **删除** |
| `POST /chatbot/chat`、`ChatbotService.chat`、客服 `ChatbotChain` | **删除** |
| `langgraph` | **必选依赖** |
| `CHATBOT_INTENT_OUTPUT_LABELS` | 增加 `hybrid_qa` |
| 意图规则/LLM/BERT | 增加 hybrid 判别与单测 |
| 企业级方案 / framework-guide / oncall / OpenAPI | 写明**仅流式**；去掉关图回滚、非流式下线「分阶段保留」叙述 |

---

## 6. 非目标（本期不做）

- 为无 `langgraph` 保留任何静默/顺序兜底
- 保留或兼容 `CHATBOT_GRAPH_ENABLED`
- 保留非流式 `/chat` 兼容窗口（**本期直接删除**）
- 将 token 级 SSE 完整实现为 LangGraph 普通节点的唯一执行方式
- 用 Hybrid 替代综合分析智能体 / 看图诊断
- 删除 sessions/upload/stop 等流式配套接口

---

## 7. 工作量量级（粗估）

| 包 | 粗估 | 依赖 |
|----|------|------|
| P1-a～d | 小 | 依赖与配置、删 Legacy/顺序回退 |
| P1-e | 小～中 | 删非流式 API/Service/Chain + 调用方与文档 |
| P2 | 中 | P1 后更干净 |
| P3 | 中～大 | 意图评测集 + 综合 prompt |

---

**文档版本**：2026-08-08 · 修订：Stream-only（删除非流式接口与链路）；不保留无 langgraph 顺序兜底；删除 `CHATBOT_GRAPH_ENABLED`。
