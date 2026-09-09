# 地降所项目 — AI 问答（智能客服）改造方案

> **版本**：2026-09-09  
> **状态**：方案稿（待按阶段实施）  
> **分支**：`dev_djs`（北京市地面沉降监测 / 地降所）  
> **范围**：四大板块之 **「智能问答」** → 算法侧 **`/chatbot/*`（`ChatbotService` + `ChatbotLangGraphRunner`）**；角色切为 **地面沉降分析专家**；回答须专业、可溯源。  
> **明确边界**：**不是**左侧「数据查询」页（`/data-query-agent/*`）；**不是**「自动报告」（`/analysis-agent/*`）；**不是**「知识库」管理台（`/rag/*` 管理面）。  
> **需求与资料**：  
> - 实施方案：`docs/地降所需求及数据相关/实施方案/北京市地面沉降监测系统运行（2026年）—基于AI大模型的地面沉降数据智能分析与运维能力提升项目实施方案 - 修改稿 - 20260811.docx`（§6.1 智能问答）  
> - 业务库：`docs/地降所需求及数据相关/数据库结构及逻辑/数据库说明.md`、`226大模型数据库.docx`  
> - 原型：[京地沉降监测智算 G-RAG](https://aistudio.google.com/apps/13d167ab-5245-4b70-b706-133e8d723e82?showPreview=true&showAssistant=true&fullscreenApplet=true)（左侧 **智能问答**；同原型「数据查询」另见需求剖析）  
> - 部署预览：`https://ais-pre-6olqxdqey6dy3cjljwozif-596480797968.asia-east1.run.app/`  
> **关联落地文档**：  
> - NL2SQL 基座：`docs/基于地降所项目改造/NL2SQL基座改造.md`（`NL2SQL_BUSINESS_DOMAIN=subsidence`）  
> - RAG 管理：`docs/基于地降所项目改造/RAG基座改造和前端功能及接口调用说明.md`  
> - 数据查询页：`docs/基于地降所项目改造/数据查询智能体实现方案.md`  
> - 前端总表：`docs/基于地降所项目改造/改造后前端UI及对应接口调用说明/地降所项目前端UI及对应接口调用说明.md`（**§一 智能问答** 待按本文补齐）  
> - 企业级客服基线：`enterprise-level_transformation_docs/企业级智能客服 LangGraph 框架实现方案.md`

---

## 0. 改造结论摘要

### 0.1 一句话

把现网仍偏 **锅炉四管客服** 的 `/chatbot` 链路，改造成面向 **北京市地面沉降监测** 业务人员的 **「地面沉降分析专家」对话助手**：主路径为 **专业 RAG 问答（带引用）**；对话内可选 **受控 NL2SQL 查数 + LLM 专业解读**；**用户可见回答与结束帧不得展示 SQL**；与「数据查询」台（表+HUD+选库 HITL）严格分工。

### 0.2 与其它三大板块的分工（必须一致）

| 板块 | 前端入口 | 算法入口 | AI 问答是否承担 |
|------|----------|----------|-----------------|
| **智能问答** | 侧栏「智能问答」 | **`POST /chatbot/chat/stream`** | **是（本文）** |
| 自动报告 | 侧栏「自动报告」 | `/analysis-agent/*` | 否 |
| 数据查询 | 侧栏「数据查询」 | `/data-query-agent/*`（表+HUD，可 HITL 选库） | **否**（勿把查询台请求打到客服） |
| 知识库 | 侧栏「知识库」 | `/rag/*` 管理面 | 否（问答只 **消费** 已摄入知识） |

```text
用户在「智能问答」提问
  → /chatbot/chat/stream
       ├─ kb_qa / clarify /（可选）smalltalk…  → RAG / 话术
       ├─ data_query                          → NL2SQL 取数 → LLM 收紧分析（对话式 Markdown）
       └─ hybrid_qa                           → NL2SQL ∥ RAG → 综合短答
用户在「数据查询」点「解析 SQL」
  → /data-query-agent/run-stream（结构化表 + HUD；默认不暴露 SQL）
```

### 0.3 现状差距（锅炉 → 地降）

| 维度 | 现网 `dev_djs` 客服默认 | 地降所目标 |
|------|------------------------|------------|
| 身份 / Prompt | `boiler_v1`：「火电厂燃煤锅炉与承压系统技术专家」 | **地面沉降分析专家**（监测、规范、成因、防控、运维） |
| 知识域 | 锅炉规程 / 本厂 `Power_plant_knowledge` / 「事故案例」 | 沉降规范、政策制度、监测方法、历史成果、设备手册等（RAG namespaces） |
| 本厂锁库 | `CHATBOT_PLANT_KB_*` 默认开，boost「华电五彩湾…」 | **关闭或改为地降侧「本区/本市」策略**（勿再 boost 电厂名） |
| 相似案例 | 锅炉管材故障门控 + `事故案例` namespace | **默认关**；若要做「历史沉降案例」须换 namespace 与判定词 |
| 意图词表 | 台账/缺陷单/号炉等锅炉启发式 | 行政区/站点/沉降速率/分层标·基岩标/水位等 |
| NL2SQL 域 | 依赖部署 `NL2SQL_BUSINESS_DOMAIN` | 必须 **`subsidence`** + 8 表白名单（见库说明） |
| 查数结果呈现 | 可有 `meta.nl2sql_sql`（默认 env 关，但前端若展示会露出） | **正文与 UI 均不展示 SQL**（对齐原型智能问答体验与用户明确要求） |
| HITL | 客服无图中断；仅占位 `handoff_human` | **本期仍不做 HITL**（选库 HITL 仅属数据查询页） |
| 前端契约 | `地降所…前端UI…md` §一为空 | 需补齐 SSE / 引用 / 会话 / 推荐问 |

### 0.4 本期原则

1. **角色专业优先**：所有生成路径（RAG 主答、NL2SQL 收紧分析、Hybrid 综合、澄清话术）统一专家人设与术语。  
2. **有据可查**：专业问答强制引用溯源；无足够依据则拒答/澄清，不编造。  
3. **查数不泄 SQL**：对话式查数只给业务可读结论（表/解读）；`nl2sql_sql` 不对前端展示（联调可单独开关，默认关）。  
4. **编排复用**：继续 Stream-only + Graph-only（`clarify` / `data_query` / `kb_qa` / `hybrid_qa`）；不另起一套问答服务。  
5. **配置包驱动**：Prompt、意图词表、RAG 默认 namespace、plant_kb、相似案例均可用 env/配置切换，避免硬编码锅炉逻辑泄漏到地降部署。

---

## 1. 需求映射（实施方案 §6.1 + 原型）

### 1.1 实施方案 §6.1（摘录对齐）

| 条款 | 需求要点 | 客服落点 |
|------|----------|----------|
| **6.1.1 通用问答** | 政策解读、平台操作、业务流程、常见概念；意图路由；无法确定来源时提示「建议核对制度或人工确认」 | `kb_qa`（操作/制度类 namespace）或轻量固定指引；低置信 → `clarify` / 拒答文案 |
| **6.1.2 专业知识问答（RAG）** | 解析→切片→向量化→混合检索→重排→**带引用生成**；无依据不编造；低置信澄清或转人工 | `kb_qa` + C-RAG + `rag_citations` / `citation_ref`；`handoff_human` 占位可选放量 |
| **会话** | ≥10 轮上下文摘要量级；用户隔离；超时清理；敏感会话不进训练 | `ConversationManager` + `CHATBOT_HISTORY_LIMIT` / `CONV_*`；部署侧 TTL |
| **部署** | 模型/知识库/应用在政务外网；业务库只读远程 | 与 NL2SQL/RAG 同栈；客服不复制整库 |

验收口径（实施方案表 9-2 量级，落地时以合同为准）：专业问答检索召回率、准确率、幻觉率；引用可追溯；通用问答可用。

### 1.2 原型「智能问答」板块（产品预期）

原型系统名 **「京地沉降监测智算 G-RAG」**，智能问答定位为 **RAG 增强对话（GRAG-Core）**：

- 多轮对话、专业语气、沉降领域问题；  
- 答案应带来源感（文档/章节）；与「数据查询」页的 **SQL 编译器 / 结果表 / HUD** 分离；  
- **用户明确要求**：若对话内走数据查询链路，返回信息中 **不要最下方的 SQL 信息**（即：不要在回答末尾或详情区贴 SQL）。

> 说明：公开原型页需 Google 登录，交互细项以实施方案 §6.1 + 本文为准；「数据查询」页交互细节见 `智能数据查询及分析-需求剖析.md`（**勿**把查询台过滤器/感知树做成客服能力）。

### 1.3 业务数据口径（对话内查数时）

与 `数据库说明.md` 一致（客服 `data_query` / `hybrid_qa` 经 NL2SQL 基座）：

| 项 | 约定 |
|----|------|
| 库 | PostgreSQL `dmcj`；方言 `postgres` |
| 主分析表 | `t_data_wash_fcb`（分层标）、`t_data_wash_jyb`（基岩标） |
| 辅助 | 地下水 / 孔压 / GNSS / 光纤 / 气象 + 维表 `t_station` |
| 范围维 | **行政区 + 监测站点 + 时间**（非锅炉「号炉/受热面」） |
| 周期沉降 | 窗内 `total_settle` 终值 − 初值 |
| 符号 | 沉降负值表示下沉，解读时勿擅自取绝对值 |

对话内查数 **默认可不做** 数据查询页的「库 HITL」；问不清监测类型时可：澄清（`clarify`）、或规则默认分层标（`fcb`，与查询台/语义层对齐），**禁止**让基座无约束扫全库乱答。

---

## 2. 目标能力与用户可见行为

### 2.1 目标能力画像

```text
【角色】地面沉降分析专家（监测数据、规范、成因机理、防控建议口径）
        │
        ▼
意图：clarify | kb_qa | data_query | hybrid_qa
        │
   ┌────┼──────────────┬────────────────┐
   ▼    ▼              ▼                ▼
澄清  专业RAG问答   对话式查数+解读   查数∥RAG综合
        │              │                │
        └──────────────┴────────────────┘
                    │
                    ▼
SSE：delta / citation_ref / finished.meta
（meta 可含 used_rag / used_nl2sql / rag_citations / suggested_questions；
 **不对用户展示 SQL 文本**）
```

### 2.2 推荐问法样例（验收用例）

| 类型 | 用户问句示例 | 期望意图 | 期望表现 |
|------|--------------|----------|----------|
| 专业规范 | 「分层标与基岩标监测的差异是什么？」 | `kb_qa` | 专家语气 + `[n]` 引用 |
| 政策流程 | 「地面沉降防控相关制度如何查阅？」 | `kb_qa` / 通用 | 有据则引；无据则提示核对 |
| 对话查数 | 「通州区近三个月沉降较大的分层标站点有哪些？」 | `data_query` | Markdown 结论/表；**无 SQL** |
| 综合 | 「查出朝阳区年沉降偏大的点，并结合规范说明关注要点」 | `hybrid_qa` | 数表结论 + 文档机理；**无 SQL** |
| 澄清 | 「这个呢」 | `clarify` | 专业口径澄清，非锅炉话术 |

### 2.3 明确不做（本期）

- 不做查询台 UI：感知树、多维过滤器、行内 HUD、选库 HITL、CSV 导出。  
- 不做自动报告章节合成（归 `/analysis-agent`）。  
- 不做知识库上传/摄入管理（归 `/rag` 管理面）。  
- 不做客服侧 LangGraph HITL interrupt/resume。  
- 不在用户可见回答中输出 SQL、表物理名堆砌、连接串。  
- 不编造岩性/防灾策略等库中不存在字段（与数据查询 HUD `unavailable` 原则一致）。

---

## 3. 改造项清单（按优先级）

### P0 — 必须（否则仍像锅炉客服）

| ID | 改造项 | 说明 | 主要落点 |
|----|--------|------|----------|
| **P0-1** | **专家身份 Prompt** | 新增 `subsidence_v1`（或 `djs_v1`）模板；默认 `CHATBOT_PROMPT_DEFAULT_VERSION` 切到该版本 | `configs/prompts.yaml`；`CHATBOT_PROMPT_DEFAULT_VERSION` |
| **P0-2** | **关闭/替换本厂电厂锁库** | `CHATBOT_PLANT_KB_ENABLED=false`；或改为地降「本市/本区」语义（若产品需要）并更换 namespace / boost 名 | `app/core/config.py` 部署 env；`chatbot_rag_scope.py` 词表 |
| **P0-3** | **保持相似案例默认关** | `CHATBOT_SIMILAR_CASE_ENABLED=false`；故障门控词勿用锅炉爆管等 | env；可选后续「沉降案例」专项 |
| **P0-4** | **意图词表地降化** | `_DATA_MARKERS` / `_CONCEPTUAL_MARKERS` / 硬闸补充：沉降、分层标、基岩标、水位、孔压、GNSS、行政区、站点、回弹等；弱化号炉/缺陷单 | `chatbot_intent_rules.py`；LLM/BERT 示例与标签说明 |
| **P0-5** | **对话查数不展示 SQL** | ① 默认 `CHATBOT_EXPOSE_NL2SQL_SQL_IN_META=false`；② 收紧分析 / Hybrid 综合 prompt **禁止输出 SQL**；③ 前端 **禁止**渲染 `meta.nl2sql_sql` 与回答底部 SQL 块 | `chatbot_nl2sql_answer.py`；分析 system prompt；前端 §一 |
| **P0-6** | **NL2SQL 域打穿** | 问答进程 `NL2SQL_BUSINESS_DOMAIN=subsidence`；库表白名单与语义包就绪 | 部署 + `NL2SQL基座改造.md` |
| **P0-7** | **澄清/占位话术去锅炉化** | `clarify` / `unsafe` / `handoff` / `smalltalk` 固定文案改为沉降业务语气 | `chatbot_graph_runner.py` 节点文案 |

### P1 — 专业 RAG 与引用体验

| ID | 改造项 | 说明 | 主要落点 |
|----|--------|------|----------|
| **P1-1** | **知识 namespaces 规划** | 与知识库管理台对齐：法规/规范/监测方法/运维手册/历史报告等；侧栏排除 `nl2sql_*` | RAG 摄入约定；`rag_scope` |
| **P1-2** | **引用展示对齐实施方案** | 继续 SSE `citation_ref` + `meta.rag_citations`（文档名、章节、预览）；无依据拒答文案专业化 | 现网已有；验收 + 前端渲染 |
| **P1-3** | **FAQ 软直通保留** | 高分规范 FAQ 仍可跳过历史防带偏；intent 仍限 `kb_qa` | `chatbot_faq_soft_direct.py` |
| **P1-4** | **关联推荐问地降化** | `chatbot_follow_up` 规则表/LLM 提示改为沉降话题（区站、规范、成因、防控） | `chatbot_follow_up.py` |
| **P1-5** | **前端 §一补齐** | 会话列表、SSE、引用角标、推荐问、停止流；**不渲染 SQL** | `地降所项目前端UI及对应接口调用说明.md` §一 |

### P2 — 对话查数专业化与 Hybrid

| ID | 改造项 | 说明 | 主要落点 |
|----|--------|------|----------|
| **P2-1** | **收紧分析 Prompt 地降化** | 去掉「号炉」等锅炉约束；强调行政区/站点/`total_settle`/负值下沉；专业解读结构 | `chatbot_nl2sql_answer.py`、`chatbot_nl2sql_display.py` |
| **P2-2** | **查数默认监测类型** | 未指明库时默认 `fcb`（或产品确认 fcb+jyb 策略）；与语义层一致 | 依赖 NL2SQL 语义包；客服侧可传 scope 提示 |
| **P2-3** | **Hybrid 综合约束** | 已有「数值以查询结果为准、机理以知识库为准」；文案改为沉降术语；失败降级文案专业化 | `_node_hybrid_synthesize` |
| **P2-4** | **错误文案** | NL2SQL 失败用户文案去掉锅炉「台账/缺陷」口吻 | `format_nl2sql_user_error` |

### P3 — 可选增强

| ID | 改造项 | 说明 |
|----|--------|------|
| **P3-1** | 放量 `handoff_human` | 低置信专业问答「建议人工确认」与实施方案对齐（仍非真人接入） |
| **P3-2** | 沉降「相似案例」 | 新 namespace + 非锅炉门控（仅当产品要「历史沉降案例」块） |
| **P3-3** | 意图 BERT 四分类重训 | 含 `hybrid_qa`；否则 bert 后端继续规则兜底 mixed |
| **P3-4** | 指代消解 yaml | `chatbot_anaphora.yaml` 示例改为沉降对话 |

---

## 4. 角色与 Prompt 设计（P0-1 核心）

### 4.1 建议新增模板 `subsidence_v1`

在 `configs/prompts.yaml` 的 `chatbot:` 下新增版本（示意结构，实施时落全文）：

```yaml
chatbot:
  - version: subsidence_v1
    weight: 1.0
    description: 北京市地面沉降监测 — 分析专家（地降所默认）
    content: |
      【角色】你是面向北京市地面沉降监测与防控业务的分析专家，熟悉分层标/基岩标、地下水与孔压、GNSS/光纤/气象等监测手段，
      以及相关技术标准、监测规范、政策制度与运维流程。回答使用专业、克制、可核查的中文，避免口语空话。

      【知识使用】有知识片段时：区分事实与推断；须按系统编号做 [n] 引用；无足够依据时明确说明「知识库暂无足够依据」，
      并建议核对现行制度或咨询业务负责人；禁止编造规范条文、站点数据或成因结论。

      【结构化数据】若系统已提供查询结果，只能基于给定结果归纳；禁止编造测点读数、SQL、表名或未给出的统计值。
      沉降量符号：负值通常表示下沉，解读时保持符号语义，勿擅自改为绝对值。
      禁止在回答中输出 SQL 语句或「底层查询语句」区块。

      【回答结构】先给直接结论 → 再给依据（引用或数据）→ 必要时补充机理/关注建议；宽泛列举题仅按检索片段作答。
      【边界】不替代正式行政结论与盖章报告；重大防控决策须提示以监测单位审核与现行规范为准。
```

部署：

```env
CHATBOT_PROMPT_DEFAULT_VERSION=subsidence_v1
```

保留 `boiler_v1` 供锅炉环境；地降部署不得默认锅炉模板。

### 4.2 收紧分析 / Hybrid 同步改写

- `chatbot_nl2sql_answer` 中分析 system / 用户错误文案：去锅炉「号炉」规则，改地降口径。  
- Hybrid 综合块标题可改为「【监测查询结果】」「【规范与知识依据】」。

---

## 5. 意图识别改造（P0-4）

### 5.1 保持标签集合

放量标签不变：`kb_qa` | `clarify` | `data_query` | `hybrid_qa`（占位 `unsafe` / `handoff_human` / `smalltalk` 仍默认不产出）。

### 5.2 规则层词表方向

| 方向 | 增加（示例） | 弱化/删除 |
|------|--------------|-----------|
| 查数 `_DATA_MARKERS` | 沉降、累计沉降、回弹、分层标、基岩标、水位、埋深、孔压、GNSS、位移、光纤、气象、通州/朝阳…、监测点/站点、年沉降 | 过度依赖「缺陷单/工单」等锅炉词（可保留通用「查询/统计/列出」） |
| 概念 `_CONCEPTUAL_MARKERS` | 成因、机理、规范、规程、防控、监测方法、分层标原理… | — |
| 混合 → `hybrid_qa` | 「查出…并结合规范说明…」 | 已落地 `mixed_hybrid`，需用新词表触发 |

意图 LLM 示例与标签定义同步改为沉降场景（见 `chatbot_intent_llm.py`）。

### 5.3 与数据查询页意图的差异

| | 智能问答 | 数据查询页 |
|--|----------|------------|
| 库意图 | 可选澄清 / 默认 fcb；**无 HITL 弹窗** | 意图1 + **HITL 选库** |
| 输出 | 自然语言 + 可选引用 | 结构化 `list` + `hud_by_entity` |
| SQL | **用户不可见** | 默认不暴露；`expose_sql` 仅联调 |

---

## 6. 数据查询链路（对话内）与「不要 SQL」

### 6.1 现网行为（保留编排）

```text
data_query
  → _node_nl2sql_answer（defer_analysis_stream=True）
  → NL2SQLService.query（subsidence 配置包）
  → 挂 nl2sql_analysis_stream_plan
  → Runner _emit_nl2sql_analysis_stream（LLM 收紧分析 → 一次 delta）
```

`hybrid_qa`：并行 NL2SQL（非 defer）+ RAG → synthesize → 流式综合答。

### 6.2 「不要最下方 SQL」落地要求

| 层 | 要求 |
|----|------|
| 算法正文 | 分析 LLM 禁止输出 SQL；后处理若检出 \`\`\`sql 块则剥离 |
| `finished.meta` | 生产默认 **不传或前端忽略** `nl2sql_sql`；保持 `CHATBOT_EXPOSE_NL2SQL_SQL_IN_META=false` |
| 前端 | 智能问答气泡 **不得** 渲染 SQL 折叠区/页脚；与数据查询页 `expose_sql` 默认 false 一致 |
| 日志/审计 | SQL 可仅写服务端审计（哈希/摘要），不进 SSE |

> 与数据查询智能体一致：联调需要看 SQL 时用专用开关，**不得**作为业务用户默认体验。

### 6.3 依赖 NL2SQL 基座

对话查数质量取决于：

- `NL2SQL_BUSINESS_DOMAIN=subsidence`  
- `configs/nl2sql_business/subsidence/` 语义与表白名单  
- PG 只读连通与字段语义覆盖  

详见 `NL2SQL基座改造.md`；客服侧不重复实现 Schema 链接。

---

## 7. RAG / 本厂锁库 / 相似案例

### 7.1 RAG

- 继续 Hybrid/Agentic + C-RAG + 引用流。  
- 知识内容换地降文档（实施方案：≥500 份、引用评测）。  
- 管理台 namespaces 与问答召回一致；排除 `nl2sql_schema` 等三库出现在「知识问答」侧栏即可（管理文档已约定）。

### 7.2 `plant_kb`

地降部署建议：

```env
CHATBOT_PLANT_KB_ENABLED=false
```

若产品需要「本市/本区」锁定某知识库，另立需求改 `chatbot_rag_scope` 标记词与 namespace，**禁止**继续使用 `Power_plant_knowledge` + 华电厂名 boost。

### 7.3 相似案例

```env
CHATBOT_SIMILAR_CASE_ENABLED=false
```

锅炉故障门控与「事故案例」namespace 不适用于沉降默认路径。

---

## 8. HITL 结论

| 能力 | 智能问答 | 数据查询页 |
|------|----------|------------|
| LangGraph interrupt / resume | **无（本期不做）** | 有（选库 HITL） |
| `handoff_human` | 占位固定话术，非真人 | — |
| `clarify` | 自动澄清，非人工审批 | — |

实施方案中「低置信转人工」在客服侧落地为：**文案提示人工确认** 或可选放量占位节点，**不**做会话挂起等待坐席。

---

## 9. 配置与部署清单（地降所问答进程）

```env
# 身份
CHATBOT_PROMPT_DEFAULT_VERSION=subsidence_v1

# 意图
CHATBOT_INTENT_ENABLED=true
CHATBOT_INTENT_BACKEND=rules
CHATBOT_INTENT_OUTPUT_LABELS=kb_qa,clarify,data_query,hybrid_qa

# 去锅炉域特性
CHATBOT_PLANT_KB_ENABLED=false
CHATBOT_SIMILAR_CASE_ENABLED=false

# 查数不展示 SQL
CHATBOT_EXPOSE_NL2SQL_SQL_IN_META=false
CHATBOT_NL2SQL_ROUTE_ENABLED=true
CHATBOT_NL2SQL_LLM_ANALYSIS_ENABLED=true

# NL2SQL 基座
NL2SQL_BUSINESS_DOMAIN=subsidence
NL2SQL_SQL_DIALECT=postgres
# DB_* 指向 dmcj 只读库（以运维为准）

# 会话（对齐「约 10 轮」量级，按产品再调）
CHATBOT_HISTORY_LIMIT=20
CONV_MAX_HISTORY_MESSAGES=50
```

镜像须安装 **`langgraph`**（客服硬依赖）。

---

## 10. 前端改造要点（填入 UI 说明 §一）

建议在 `地降所项目前端UI及对应接口调用说明.md` **「一、智能问答」** 中写明：

1. 路由建议 `/chat` 或 `/qa`；标题可用「智能问答」/「GRAG 对话」。  
2. 主接口：`POST /chatbot/chat/stream`；停止：`POST /chatbot/chat/stop`；会话：`/chatbot/sessions*`。  
3. 渲染：`delta` 正文、`citation_ref` 角标、`finished.meta.rag_citations` 来源列表、`suggested_questions`。  
4. **禁止**：展示 `meta.nl2sql_sql`、回答底部 SQL、跳转数据查询台契约事件。  
5. 鉴权：`Authorization: Bearer <SERVICE_API_KEY>`。  
6. 空态文案与专家人设一致（勿写锅炉欢迎语）。

---

## 11. 实施阶段建议

| 阶段 | 内容 | 出口标准 |
|------|------|----------|
| **S1** | P0-1～P0-7 + 部署 env | 默认身份为沉降专家；无电厂 boost；澄清非锅炉话术 |
| **S2** | P0-5 验收 + P1 引用/前端 §一 | 专业问答有引用；对话查数用户侧零 SQL |
| **S3** | P2 查数/Hybrid 文案与默认表策略 | 通州/朝阳等样例问数专业可读 |
| **S4** | 评测集（规范问 + 查数问 + 混合问） | 对齐实施方案准确率/幻觉/引用指标（合同口径） |

---

## 12. 验收标准（最小）

1. **身份**：任意寒暄/专业问，回答不以锅炉专家自居；术语符合沉降监测。  
2. **RAG**：规范类问题有 `rag_citations` / 角标；无依据时拒答或澄清，不编造条文。  
3. **对话查数**：典型区+时间+分层标问句能出业务结论；**页面与正文均无 SQL**。  
4. **Hybrid**：混合问同时体现数据结论与规范依据（或单臂降级且 `hybrid_degraded` 可观测）。  
5. **边界**：数据查询页仍只走 `/data-query-agent`；问答页不出现选库 HITL。  
6. **配置**：地降部署默认 `subsidence_v1` + `plant_kb` 关 + `expose_sql` 关。

---

## 13. 风险与开放问题

| 风险/问题 | 建议 |
|-----------|------|
| 知识库未达标导致专业问答空 | 与阶段三知识库建设联动；空库时明确拒答 |
| NL2SQL 未切 subsidence | 问答查数串锅炉表或失败；部署检查清单强制项 |
| 前端仍展示 `nl2sql_sql` | 前后端双关；验收用例专门扫 UI |
| 「默认 fcb 还是 fcb+jyb」 | 对话查数建议单库默认 fcb；双库列表仅属查询台 Java 浏览 |
| 原型智能问答细交互未完全抓取 | 以实施方案 §6.1 为准；联调时用预发地址补截图 |

---

## 14. 改造项追踪表（实施用）

| ID | 状态 | Owner | 备注 |
|----|------|-------|------|
| P0-1 Prompt `subsidence_v1` | 待办 | | |
| P0-2 plant_kb 关闭/替换 | 待办 | | |
| P0-3 相似案例保持关 | 待办 | | |
| P0-4 意图词表 | 待办 | | |
| P0-5 禁止用户侧 SQL | 待办 | | 含前端 |
| P0-6 NL2SQL domain | 待办 | | 依赖基座 |
| P0-7 固定话术 | 待办 | | |
| P1-* | 待办 | | |
| P2-* | 待办 | | |
| 前端 UI §一 | 待办 | | |
| 评测集 | 待办 | | |

---

## 附录 A. 现网客服能力速查（改造基线）

- 入口：仅 `POST /chatbot/chat/stream`（无非流式 `/chat`、无 Legacy）。  
- 意图：`clarify` / `data_query` / `kb_qa` / `hybrid_qa`。  
- 图尾：`finalize` → `similar_cases_retrieve` → `suggest_followups`。  
- 查数：NL2SQL →（可选）LLM 收紧分析流。  
- HITL：无。  

详见企业级 LangGraph 方案与 `docs/智能客服LangGraph收敛与Hybrid意图改造方案.md`。

## 附录 B. 库表与 library_id（对话查数引用）

| library_id | 名称 | 物理表 |
|------------|------|--------|
| `fcb` | 分层标 | `t_data_wash_fcb` |
| `jyb` | 基岩标 | `t_data_wash_jyb` |
| `dxswj` | 地下水 | `t_data_wash_dxswj` |
| `kxsylj` | 孔隙水压力 | `t_data_wash_kxsylj` |
| `gnss` | GNSS | `t_data_wash_gnss` |
| `gq` | 光纤 | `t_data_wash_gq` |
| `qxz` | 气象站 | `t_data_wash_qxz` |
| — | 站点维表 | `t_station` |
