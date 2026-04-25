# 在线业务值班排障清单（一页版）

> 适用范围：**整体项目在线业务**值班排障总入口。  
> 当前版本覆盖：**智能客服（已企业级完整实现）**。  
> 后续将按模块成熟度逐步补齐：`/llm/infer`、`/analysis/run-with-payload`、`/analysis/run-with-nl2sql`、`/nl2sql/query`、`/small-model/*` 等。

---

## 1. 值班总原则（先止血，再定位）

- 先确认影响范围：单用户/单接口/全站。
- 先恢复可用性：必要时启用已存在的降级与回退开关。
- 所有操作可回滚：记录修改前后配置与时间点。
- 优先看事实：健康检查、错误日志、接口真实响应，不靠猜测。

---

## 2. 5 分钟快速检查（智能客服）

按顺序执行，命令可直接复制：

```bash
# A. 应用健康
curl -s "http://127.0.0.1:8083/health/"

# B. vLLM 健康
curl -s "http://127.0.0.1:8000/health"

# C. EasySearch 健康（自签名场景）
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"

# D. 智能客服流式接口冒烟
curl -N -X POST "http://127.0.0.1:8083/chatbot/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"oncall","session_id":"s1","query":"你好","enable_rag":false,"enable_context":false}'
```

期望结果：

- `A/B/C` 均返回健康状态（`ok` / `green|yellow`）。
- `D` 至少出现一条 `delta` 与一条 `finished=true` 事件。

---

## 3. 智能客服接口级排障（主用）

当前主用接口：`POST /chatbot/chat/stream`（`/chatbot/chat` 为兼容保留）。

### 3.1 症状：接口 5xx / 超时

先查：

```bash
docker compose -f app/app-deploy/docker-compose-mx.yml logs -f models-app
```

重点看三类错误：

- 连接 vLLM 失败：检查 `LLM_DEFAULT_ENDPOINT` 是否为 `http://vllm-service:8000/v1`
- 连接 EasySearch 失败：检查 `RAG_ES_HOSTS`、`RAG_ES_USERNAME`、`RAG_ES_PASSWORD`
- 图执行异常：确认 `CHATBOT_FALLBACK_LEGACY_ON_ERROR=true` 是否生效

### 3.2 症状：SSE 无增量或只返回结束帧

先查配置：

- `CHATBOT_GRAPH_ENABLED=true`
- `CHATBOT_INTENT_ENABLED=true`
- `CHATBOT_INTENT_OUTPUT_LABELS=kb_qa,clarify`

再查响应尾帧 `meta`：

- `status` 是否为 `failed` / `aborted`
- `terminate_reason` 是否提示超时或异常
- `duration_ms` 是否接近 `MAX_GRAPH_LATENCY_MS`

### 3.3 症状：会话上下文丢失

先查：

- `REDIS_URL=redis://redis:6379/0`（或可用外部 Redis）
- `CONV_SESSION_TTL_MINUTES` 是否过小
- `CONV_MAX_HISTORY_MESSAGES` 与 `CHATBOT_HISTORY_LIMIT` 是否合理

快速验证：同一 `user_id + session_id` 连续发两轮问题，第二轮是否引用第一轮语境。

### 3.4 症状：启用 RAG 但回答像“没走知识库”

先查：

- 请求体是否 `enable_rag=true`
- 是否已完成知识摄入（`/rag/*`）
- EasySearch 是否健康、凭据是否匹配

再查尾帧 `meta`：

- `used_rag` 是否为 `true`
- `retrieval_attempts` 是否大于等于 1
- `rag_engine` 是否符合配置（`agentic` / `hybrid`）

---

## 4. 一键止血策略（仅限智能客服）

当线上故障持续、需要优先恢复可用性：

1. 在 `app/app-deploy/.env` 保持：
   - `CHATBOT_FALLBACK_LEGACY_ON_ERROR=true`
2. 若图链路持续异常，可临时：
   - `CHATBOT_GRAPH_ENABLED=false`
3. 重启应用：

```bash
cd app/app-deploy
docker compose up -d --build
```

回滚原则：

- 问题修复后，尽快恢复 `CHATBOT_GRAPH_ENABLED=true`，并完成冒烟验证。

---

## 5. 值班交接模板（建议复制到工单）

```text
[时间]
[影响范围] 用户量/接口/错误率
[现象] 例如 /chatbot/chat/stream 5xx 上升
[已执行动作] 配置调整、重启、回滚
[当前状态] 已恢复/部分恢复/未恢复
[待跟进] 根因定位、代码修复、文档补充
```

---

## 6. 后续扩展计划（占位）

本文件是“整体项目在线业务”值班总入口。后续模块达到企业级完整实现后，按同一结构补充：

- `/llm/infer`
- `/analysis/run-with-payload`
- `/analysis/run-with-nl2sql`
- `/nl2sql/query`
- `/small-model/channel/*`

建议每个模块都补齐：5 分钟检查、接口级症状树、止血开关、交接模板。

