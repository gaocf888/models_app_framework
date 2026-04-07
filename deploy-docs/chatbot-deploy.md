# 智能客服部署与配置指南（端到端）

> 面向：负责在测试/生产环境落地本项目「智能客服」能力的开发 & 运维同学。  
> 目标：按本文完成后，能够通过 HTTP 调用 `/chatbot/chat` / `/chatbot/chat/stream`，并正确联通大模型、RAG 知识库与会话存储。  
> 当前推荐主链路为 **LangGraph + SSE 流式接口**，`/chatbot/chat` 保留为兼容接口。
> 值班排障建议配合：`deploy-docs/online-services-oncall-runbook.md`（当前先覆盖智能客服）。

---

## 0. 最短上线路径（推荐先执行）

适用于“先打通，再细调”的测试/生产首发场景。

```bash
# 1) EasySearch
cd rag_db-deploy
cp .env.example .env
docker compose -f docker-compose.easysearch.yml --env-file .env up -d

# 2) vLLM
cd ../vllm-deploy
cp .env.example .env
chmod +x deploy.sh
./deploy.sh

# 3) app（FastAPI + Redis）
cd ../app/app-deploy
cp .env.example .env
docker compose up -d --build
```

最小可用验证：

```bash
curl -s "http://127.0.0.1:8000/health"      # vLLM
curl -s "http://127.0.0.1:8083/health/"      # app
curl -N -X POST "http://127.0.0.1:8083/chatbot/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"u1","session_id":"s1","query":"你好","enable_rag":false,"enable_context":false}'
```

---

## 0.1 上线前/后检查清单（运维视角）

上线前（必须）：

- [ ] `LLM_DEFAULT_ENDPOINT` 使用容器可解析地址（如 `http://vllm-service:8000/v1`），不是容器内 `127.0.0.1`
- [ ] `LLM_DEFAULT_MODEL` 与 `vllm-deploy/config/models.yaml` 的 `served_model_name` 一致
- [ ] `RAG_ES_HOSTS` / `RAG_ES_USERNAME` / `RAG_ES_PASSWORD` 与 `rag_db-deploy/.env` 完全一致
- [ ] `REDIS_URL=redis://redis:6379/0`（或已切换为可用外部 Redis）
- [ ] `CHATBOT_GRAPH_ENABLED=true` 且 `CHATBOT_INTENT_OUTPUT_LABELS=kb_qa,clarify`
- [ ] `CHATBOT_FALLBACK_LEGACY_ON_ERROR=true`（建议灰度阶段保留兜底）

上线后（5 分钟内）：

- [ ] `GET /health/` 返回 `ok`，并且容器日志无持续报错
- [ ] `/chatbot/chat/stream` 首帧可返回 `delta`，尾帧 `finished=true`
- [ ] 尾帧 `meta` 含 `status`、`intent_label`、`retrieval_attempts`、`duration_ms`
- [ ] 在同一 `user_id + session_id` 下连续两轮对话，确认上下文生效
- [ ] 若启用 RAG：`enable_rag=true` 时回答内容能体现知识库片段

---

## 1. 整体架构与涉及组件

智能客服依赖的主要组件如下：

| 层级 | 组件 | 目录 | 作用 |
|------|------|------|------|
| 大模型服务 | vLLM | `vllm-deploy/` | 提供 OpenAI 兼容的 LLM/VL 推理服务（`/v1/chat/completions`） |
| RAG 底座 | EasySearch | `rag_db-deploy/` | 提供向量 + 全文检索能力，存储知识库文档 |
| 图数据库（可选） | Neo4j | `graphrag_db-deploy/` | 提供 GraphRAG 用的图存储（当前 Chatbot 默认仍用向量 RAG） |
| 应用服务 | FastAPI | `app/app-deploy/` | 暴露 `/chatbot/*`、`/llm/*`、`/analysis/*` 等 API |
| 会话存储 | Redis | `app/app-deploy/docker-compose.yml` 内置 | 存储用户会话历史与上下文 |

调用主链路（简化）：

1. 客户端 → `POST /chatbot/chat` 或 `POST /chatbot/chat/stream`。  
2. FastAPI 路由 → `ChatbotService`。  
3. ChatbotService：  
   - 使用 `ConversationManager` 写入会话；  
   - 可选调用 `HybridRAGService` 从 EasySearch 检索上下文；  
   - 构建多模态 `messages`，调用 `VLLMHttpClient` → vLLM；  
   - 同步或流式返回结果，并写回助手消息。  
4. `ChatbotService` 默认走 LangGraph 编排（意图 + C-RAG + 统一 finalize），必要时按配置回退 legacy 顺序链路。

---

## 2. 部署顺序总览

建议按下面顺序在目标环境中部署（每一步的具体操作见后续章节）：

1. **部署 EasySearch（RAG 底座）**：`rag_db-deploy/`。  
2. **部署 vLLM（大模型服务）**：`vllm-deploy/`。  
3. 可选部署 **Neo4j（GraphRAG）**：`graphrag_db-deploy/`。  
4. **部署应用服务（FastAPI + Redis）**：`app/app-deploy/`。  
5. 验证：  
   - EasySearch / vLLM / 应用 `/health/`；  
   - 简单调用 `/chatbot/chat` 或脚本完成端到端连通性测试。

---

## 3. 部署 EasySearch（RAG 数据库）

### 3.1 准备配置

目录：`rag_db-deploy/`

1. 复制环境文件：

```bash
cd rag_db-deploy
cp .env.example .env
```

2. 根据实际环境修改 `.env`（常用项）：

| 变量 | 作用 | 示例 |
|------|------|------|
| `EASYSEARCH_IMAGE` | 镜像 | `infinilabs/easysearch:2.1.1` |
| `EASYSEARCH_CONTAINER_NAME` | 容器名 | `rag-easysearch` |
| `EASYSEARCH_NETWORK` | Docker 网络名 | `ai-stack` |
| `EASYSEARCH_PORT` | 暴露端口 | `9200` |
| `EASYSEARCH_USERNAME` | 管理员账号 | `admin` |
| `EASYSEARCH_PASSWORD` | 管理员密码 | `ChangeMe_123!`（请生产中改为强密码） |

### 3.2 启动 EasySearch

```bash
cd rag_db-deploy
docker compose -f docker-compose.easysearch.yml --env-file .env up -d
```

检查：

```bash
docker ps | grep rag-easysearch

# 健康检查（自签名证书场景）
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"
```

> 详细错误与安全配置说明见 `rag_db-deploy/README.md`。

---

## 4. 部署 vLLM（大模型服务）

### 4.1 准备模型与配置

目录：`vllm-deploy/`

1. 按仓库内 `vllm-deploy/README.md` 准备模型权重目录及 `config/models.yaml` 预设（例如 `qwen2.5-vl-7b`）。  
2. 确认 `vllm-deploy/config/vllm.yaml`、`config/models.yaml` 中的模型名称与期望一致。

### 4.2 启动 vLLM

```bash
cd vllm-deploy
chmod +x deploy.sh
./deploy.sh
```

一般会暴露为宿主机 `127.0.0.1:8000`，容器名默认为 `vllm-service`。

> 如需手动启动 compose，请使用：  
> `cd vllm-deploy/docker && docker compose --env-file ../.env up -d --build`

检查：

```bash
curl -s "http://127.0.0.1:8000/health"
```

应用侧通过环境变量：

- `LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1`  
- `LLM_DEFAULT_MODEL=<models.yaml 中的 served-model-name>`  

来访问该服务。

---

## 5. 可选：部署 Neo4j（GraphRAG）

> 智能客服当前以**向量 RAG 为主**；如果你暂不启用 GraphRAG，可跳过本节。

### 5.1 部署 Neo4j

目录：`graphrag_db-deploy/`

1. 复制环境并修改：

```bash
cd graphrag_db-deploy
cp .env.example .env
```

常用项：

| 变量 | 说明 | 示例 |
|------|------|------|
| `NEO4J_IMAGE` | 镜像 | `neo4j:5.24.0-community` |
| `NEO4J_CONTAINER_NAME` | 容器名 | `graph-neo4j` |
| `NEO4J_NETWORK` | 网络名 | `graph-stack` |
| `NEO4J_BOLT_PORT` | Bolt 端口 | `7687` |
| `NEO4J_HTTP_PORT` | Web UI 端口 | `7474` |
| `NEO4J_USERNAME` / `NEO4J_PASSWORD` | 账号密码 | `neo4j` / `ChangeMe_123!` |

2. 启动：

```bash
docker compose -f docker-compose.neo4j.yml --env-file .env up -d
```

### 5.2 应用侧启用 GraphRAG

在 `app/app-deploy/.env` 中增加：

```env
GRAPH_RAG_ENABLED=true
NEO4J_URI=bolt://graph-neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=ChangeMe_123!
NEO4J_DATABASE=neo4j
```

并确保 `GRAPH_DOCKER_NETWORK=graph-stack`，应用 compose 已将容器加入该网络。

> 详细 GraphRAG 行为（纯向量 / 纯图 / 混合）参见 `framework-guide/RAG整体实现技术说明.md`。

---

## 6. 部署应用服务（智能客服 API）

目录：`app/app-deploy/`

### 6.1 准备 `.env`

```bash
cd app/app-deploy
cp .env.example .env
```

必需关注的配置项：

#### 6.1.1 大模型

```env
LLM_DEFAULT_MODEL=qwen2.5-vl-7b-instruct
LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1
LLM_DEFAULT_API_KEY=           # 若 vLLM 启用了鉴权，在此填写
```

#### 6.1.2 RAG / EasySearch

```env
RAG_VECTOR_STORE_TYPE=es
RAG_ES_HOSTS=https://rag-easysearch:9200
RAG_ES_USERNAME=admin
RAG_ES_PASSWORD=ChangeMe_123!  # 与 rag_db-deploy/.env 中保持一致
RAG_ES_VERIFY_CERTS=false      # 自签证书时通常为 false
```

#### 6.1.3 会话（Redis）

```env
REDIS_URL=redis://redis:6379/0
CONV_SESSION_TTL_MINUTES=60
CONV_MAX_HISTORY_MESSAGES=50
```

应用的 `ConversationManager` 将优先尝试使用 Redis，会话数据可在多实例间共享。

#### 6.1.4 智能客服 LangGraph（建议显式配置）

```env
CHATBOT_GRAPH_ENABLED=true
CHATBOT_INTENT_ENABLED=true
CHATBOT_INTENT_OUTPUT_LABELS=kb_qa,clarify
CHATBOT_CRAG_ENABLED=true
CHATBOT_CRAG_MAX_ATTEMPTS=2
CHATBOT_CRAG_MIN_SCORE=0.55
CHATBOT_RAG_ENGINE_MODE=agentic
CHATBOT_RAG_ENGINE_FALLBACK=hybrid
CHATBOT_HISTORY_LIMIT=20
CHATBOT_PERSIST_PARTIAL_ON_DISCONNECT=true
CHATBOT_FALLBACK_LEGACY_ON_ERROR=true
MAX_REWRITE_QUERY_LENGTH=120
MAX_GRAPH_LATENCY_MS=20000
CHATBOT_CHECKPOINT_BACKEND=none
CHATBOT_CHECKPOINT_NAMESPACE=chatbot_graph
# CHATBOT_CHECKPOINT_REDIS_URL=redis://redis:6379/1
```

说明：

- `CHATBOT_HISTORY_LIMIT` 控制“单轮读取历史窗口”，`CONV_MAX_HISTORY_MESSAGES` 控制“会话总保留上限”；
- `CHATBOT_INTENT_OUTPUT_LABELS` 默认建议仅放量 `kb_qa,clarify`；
- 生产场景建议先保持 `CHATBOT_FALLBACK_LEGACY_ON_ERROR=true`，用于图异常兜底。

#### 6.1.5 业务数据库（NL2SQL，可选）

若智能客服暂不依赖 NL2SQL，可先维持默认；如需联通数据库：

```env
DB_URL=mysql+aiomysql://root:your_mysql_password@host.docker.internal:3306/aishare
```

#### 6.1.6 GraphRAG（可选）

如第 5 节所述：

```env
GRAPH_RAG_ENABLED=false  # 若暂不启用，可保持 false
# 启用时同时配置 NEO4J_URI / USERNAME / PASSWORD / DATABASE
```

### 6.2 启动应用栈

```bash
cd app/app-deploy
docker compose up -d --build
```

默认会启动：

- `models-app-redis`（本栈 Redis）；  
- `models-app`（FastAPI 应用）。

如需小模型 GPU profile，可额外执行：

```bash
docker compose --profile small-model-gpu up -d --build
```

详细见 `app/app-deploy/README.md` 与 `README-dev.md`。

---

## 7. 验证智能客服链路

### 7.1 健康检查

```bash
# 应用健康
curl -s "http://127.0.0.1:8080/health/"

# EasySearch 健康
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"

# vLLM 健康
curl -s "http://127.0.0.1:8000/health"
```

### 7.2 基础对话测试（无 RAG）

```bash
curl -s -X POST "http://127.0.0.1:8080/chatbot/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u1",
    "session_id": "s1",
    "query": "你好，请简单自我介绍一下。",
    "enable_rag": false,
    "enable_context": false
  }'
```

应返回形如：

```json
{
  "answer": "...",
  "used_rag": false,
  "context_snippets": []
}
```

### 7.3 启用 RAG 的对话测试

在完成 RAG 知识摄入（`/rag/...` 接口或管理脚本）后，测试：

```bash
curl -s -X POST "http://127.0.0.1:8080/chatbot/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u1",
    "session_id": "s1",
    "query": "问一个和知识库内容相关的问题",
    "enable_rag": true,
    "enable_context": true
  }'
```

期望：

- `used_rag` 为 true；  
- `context_snippets` 中有若干条与问题相关的片段。

### 7.4 流式接口（SSE，推荐主用）

```bash
curl -N -X POST "http://127.0.0.1:8080/chatbot/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "u1",
    "session_id": "s1",
    "query": "请用三句话介绍一下你自己。",
    "enable_rag": false,
    "enable_context": false
  }'
```

响应应为 `text/event-stream`，每行 `data: {...}`。典型序列：

```text
data: {"delta":"...","finished":false}
...
data: {"finished":true,"meta":{"status":"answered","intent_label":"kb_qa","retrieval_attempts":1}}
```

其中最后一条 `finished=true` 事件会携带 `meta`，用于观测本轮是否命中 RAG、意图路由与终止原因等。

---

## 8. 常见问题与排错方向（智能客服相关）

| 现象 | 排查方向 |
|------|----------|
| `/chatbot/*` 返回 5xx 或超时 | 查看 `docker compose logs -f models-app`，确认 vLLM/EasySearch 是否连通、LLM 调用是否抛异常（占位回答时会带中文提示）。 |
| `used_rag` 一直为 false | 检查 EasySearch 是否正常、RAG 知识是否完成摄入；确认 `RAG_VECTOR_STORE_TYPE=es` 且 `RAG_ES_*` 配置正确；看 `HybridRAGService` 日志。 |
| 会话不生效 / 上下文丢失过快 | 检查 `REDIS_URL` 是否配置；`CONV_SESSION_TTL_MINUTES` 与 `CONV_MAX_HISTORY_MESSAGES` 是否符合预期；确认 Redis 可用。 |
| Redis 打开后偶发报错 | 现已改为单独事件循环线程执行 Redis IO，若仍有错误，可查看 `RedisConversationStore` 日志并考虑调整 TTL/连接数。 |
| GraphRAG 打不开 / 连不上 Neo4j | 确认 `graphrag_db-deploy` 中容器与网络正常；`GRAPH_RAG_ENABLED=true` 且 `NEO4J_*` 与实际部署一致；应用 compose 中已加入 `graph-external` 网络。 |

更详细的架构与实现说明，可参考：

- `framework-guide/智能客服整体实现技术说明.md`  
- `framework-guide/RAG整体实现技术说明.md`  
- `memory-bank/01-architecture.md`  
- `app/app-deploy/README.md`（完整运维视角） 与 `README-dev.md`（开发视角）

