# 智能客服 Hybrid 意图改造方案


---

## 1. 背景与结论摘要

| # | 改造项 | 结论 | 一句话 |
|---|--------|------|--------|
| P3 | 新增 RAG+NL2SQL 综合意图 | **做** | 互斥三分流无法覆盖「既要数又要机理」；新标签 + 双臂合成 |
| HITL | Hybrid 与两次人机协同共存 | **做** | 清晰混合直走 Hybrid；HITL 留给真模糊，并支持选综合 |

**产品拍板（已落地）**

1. 测试集里「清晰混合」**不再强制**弹三按钮；改为 **Hybrid 直答**，HITL 留给真模糊。
2. 二次消歧采用 **策略 D**：保留 LLM 具体问法列表；选项升级为「拆开查数 + 拆开知识 + 可选综合」。

---

## 2. 与 zajt HITL 的关系（相对 djs）

| 分支 | Hybrid | 意图 HITL |
|------|--------|-----------|
| `dev_djs` | 已有 `hybrid_qa` 双臂 | **无** chatbot 意图 HITL |
| `dev_zajt` | 本方案对齐 djs 双臂 | **有**两轮意图 HITL，需与 Hybrid **共存** |

关键约束：规则 reason `mixed_hybrid` 含有子串 `mixed_`。若 HITL 仍用 `"mixed_" in reason` 无差别触发，会把清晰 Hybrid 再次打断。实现上必须：

- `label == hybrid_qa` 且清晰（`mixed_hybrid` / 足够置信）→ **跳过**意图 HITL；
- 其它 `mixed_*`、低置信、歧义、双标记冲突 → 仍可 HITL。

---

## 3. P3：Hybrid 意图（RAG + NL2SQL 综合）

### 3.1 动机

当前 `clarify` / `data_query` / `kb_qa` **互斥**；旧规则对混合问只能 `mixed_prefers_*` 二选一。用户常见诉求：「查出超温点 + 结合规程解释/处置」需要**两路证据再综合**。

### 3.2 新意图

- 标签：`hybrid_qa`
- 纳入 `CHATBOT_INTENT_OUTPUT_LABELS`（默认 `kb_qa,clarify,data_query,hybrid_qa`）
- `rules` / `llm` / `bert` 均可产出；规则：`data ∧ conceptual` → `hybrid_qa`，reason=`mixed_hybrid`，conf≈0.75
- NL2SQL 关闭时硬闸不产出 `data_query` / `hybrid_qa`

### 3.3 编排

```text
_route_by_intent → hybrid_qa
  → hybrid_acquire（并行）
       ├ NL2SQL：run_chatbot_nl2sql_query（defer_analysis_stream=False，综合前先拿正文）
       └ RAG：select_rag_engine → rag_scope → kb_retrieve（简化，不做 C-RAG 重试）
  → hybrid_synthesize
       ├ 仅 NL2SQL → 固定话术/表，走 no_stream_path
       ├ 仅 RAG → 复用 kb_build_messages
       └ 双臂成功 → 组装双源 system 块 → llm_messages 流式生成
  → finalize
```

HITL 开启时 zajt 走 `_run_graph_sequential` / `_execute_route`，同样调用 acquire → synthesize → finalize。

**降级**：`meta.hybrid_degraded` ∈ `{""|nl2sql|rag|both}`；单臂失败时降为纯查数或纯知识语义，双失败返回友好话术。

### 3.4 输出与产品边界

- `meta`：双臂成功时 `used_rag=true` 且 `used_nl2sql=true`；保留 `rag_citations`、`nl2sql_sql` / `nl2sql_analysis`、`hybrid_degraded`
- 关联问：Hybrid **可下发**（与纯 `data_query` 区分）
- 相似案例：同知识问答门控（非纯 `data_query`）
- **与综合分析分工**：客服 Hybrid = 短答一问一综合；长报告/多槽仍走 `/analysis/*`

### 3.5 验收（最小）

1. 纯列表问 → `data_query`；纯概念问 → `kb_qa`；清晰混合 → `hybrid_qa` 且**不弹**意图 HITL
2. 双臂成功：回答同时引用数表结论与文档依据；SSE 可含 `citation_ref`
3. SQL 失败 / RAG 空：可降级且不 5xx
4. 真模糊仍可 HITL；第四钮 / 消歧第三项可进 Hybrid

---

## 4. Hybrid + 保留两次 HITL（策略 D，已拍板）

### 4.1 总原则

| 场景 | 行为 |
|------|------|
| 清晰强混合（规则 `mixed_hybrid` / 高置信 `hybrid_qa`） | **直走 Hybrid**，跳过意图 HITL |
| 真模糊（低置信、歧义、短补充后仍不清、双标记冲突等） | 仍走 HITL；用户可选单路或综合 |

### 4.2 第一次 HITL（`intent_route_confirm`）

触发条件收窄后，按钮为四项：

| id | 文案 | 路由 |
|----|------|------|
| `route_data_query` | 查实时/台账数据 | NL2SQL |
| `route_kb_qa` | 基于知识库分析 | RAG |
| `route_hybrid_qa` | **综合查数+知识** | Hybrid 双臂 |
| `route_clarify` | 我先补充问题 | 重分类；仍失败 → 二次消歧 |

清晰混合**不再强制**出现此面板。

### 4.3 第二次 HITL（`intent_disambiguation_suggest`）

- **保留** LLM 生成的具体问法列表（不取消选项列表）。
- 选项结构升级为「**拆开 + 可选综合**」：
  - ≥1 条 `route_hint=data_query`（拆开查数）
  - ≥1 条 `route_hint=kb_qa`（拆开知识）
  - **建议** 1 条 `route_hint=hybrid_qa`（综合；问句可保留「查数+解释」）
- 规则兜底第三项为「综合查数+知识」→ `hybrid_qa`
- 点选后按 `query` + `route_hint` **直接路由**（含 `hybrid_qa`），不再跑意图分类

```text
首轮模糊 → intent_route_confirm（四钮）
  ├ 选 data / kb / hybrid → 直接执行对应路由
  └ 选补充问题 → 重分类
        ├ 已清晰（含 hybrid）→ 直答
        └ 仍模糊 → intent_disambiguation_suggest
              └ 点选：拆开 data | 拆开 kb | 可选 hybrid
```

### 4.4 与「场景 B」对齐说明

- **场景 B（Hybrid 跳过意图 HITL）**：仅针对**已判成清晰 `hybrid_qa`** 的自动综合；不是取消整条 HITL 能力。
- 两次 HITL 继续服务「说不清要查数、要知识、还是要综合」的用户；Hybrid 只是第四条/第三条合法出口。

---

## 5. 配置与文档同步

| 项 | 动作 |
|----|------|
| `CHATBOT_INTENT_OUTPUT_LABELS` | 默认含 `hybrid_qa` |
| 意图规则/LLM/BERT | 产出 `hybrid_qa`；LLM 混合提示优先综合 |
| `chatbot_intent_disambiguation` prompt | `route_hint` 含 `hybrid_qa` |
| HITL UI / API 文档 | 第四钮 `route_hybrid_qa`；消歧支持 hybrid |
| 单测 | `test_mixed_hybrid`；清晰 hybrid 不触发 HITL；消歧/按钮含 hybrid |

---

## 6. 实现落点（代码索引）

| 域 | 主要文件 |
|----|----------|
| 状态 / 标签 | `chatbot_graph_state.py`（`hybrid_qa`、`hybrid_degraded`） |
| 规则 | `chatbot_intent_rules.py`（`mixed_hybrid`） |
| LLM / BERT | `chatbot_intent_llm.py`、`chatbot_intent_bert.py` |
| 双臂编排 | `chatbot_graph_runner.py`（`hybrid_acquire` / `hybrid_synthesize`） |
| HITL 触发与动作 | `chatbot_hitl.py`、`chatbot_hitl_display.py` |
| 二次消歧 | `chatbot_intent_disambiguation.py`、`configs/prompts.yaml` |
| 配置 | `app/core/config.py`、`app/app-deploy/.env.example` |
