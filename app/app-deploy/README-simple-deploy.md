# 应用部署简明版（生产配置与上线流程）

> 本文是 `README.md` 的**精简版**，重点面向「应用配置 + 生产/测试环境部署」，不讨论代码开发细节。  
> 若需要完整说明（包括治理策略、GPU profile 细节、运维检查清单），请阅读同目录 `README.md`。

---

## 1. 组件与依赖概览

上线智能客服 / 通用 LLM API 时，通常需要以下容器：

| 能力 | 目录 | 说明 |
|------|------|------|
| 大模型推理（vLLM） | `vllm-deploy/` | 提供 OpenAI 兼容 `/v1/chat/completions` |
| RAG 向量 + 全文库 | `rag_db-deploy/` | EasySearch，存储知识库文档 |
| 图数据库（可选 GraphRAG） | `graphrag_db-deploy/` | Neo4j，当前聊天默认仍以向量 RAG 为主 |
| 应用 API | `app/app-deploy/` | FastAPI 服务，暴露 `/chatbot/*`、`/llm/*`、`/analysis/*` 等 |
| 会话存储 | `app/app-deploy/` 内置 Redis | 存储会话历史，可通过 `REDIS_URL` 切换到外部 Redis |

部署顺序推荐：**EasySearch → vLLM →（可选 Neo4j）→ 应用栈**。

---

## 2. 必改配置（`.env` 总览）

目录：`app/app-deploy/`

```bash
cp .env.example .env
```

在 `.env` 中至少确认/修改以下几块。

### 2.1 大模型（vLLM）

```env
LLM_DEFAULT_MODEL=qwen2.5-vl-7b-instruct
LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1
LLM_DEFAULT_API_KEY=        # 如 vLLM 启用鉴权，与 vLLM 侧保持一致
```

要求：

- `LLM_DEFAULT_ENDPOINT` 使用 **容器间可解析主机名**（如 `vllm-service`），不要写 `127.0.0.1`。  
- `LLM_DEFAULT_MODEL` 必须与 `vllm-deploy/config/models.yaml` 中对应模型的 `served_model_name` 一致。

### 2.2 RAG / EasySearch

```env
RAG_VECTOR_STORE_TYPE=es
RAG_ES_HOSTS=https://rag-easysearch:9200
RAG_ES_USERNAME=admin
RAG_ES_PASSWORD=ChangeMe_123!   # 与 rag_db-deploy/.env 一致
RAG_ES_VERIFY_CERTS=false       # 自签证书通常为 false
```

- `RAG_ES_HOSTS` 使用 EasySearch 容器名（默认 `rag-easysearch`）。  
- 账号密码与 `rag_db-deploy/.env`、容器内 `reset_admin_password.sh` 一致。

### 2.3 会话 / Redis

```env
REDIS_URL=redis://redis:6379/0
CONV_SESSION_TTL_MINUTES=60
CONV_MAX_HISTORY_MESSAGES=50
```

默认使用本栈内置 Redis 容器 `models-app-redis`；若要用外部 Redis，只需改成对应连接串。

### 2.4 业务数据库（NL2SQL，可选）

```env
DB_URL=mysql+aiomysql://root:your_mysql_password@host.docker.internal:3306/aishare
```

如果当前环境智能客服暂不依赖 NL2SQL，可保留默认或指向测试库。

### 2.5 GraphRAG（可选）

```env
GRAPH_RAG_ENABLED=false
# 启用时：
# GRAPH_RAG_ENABLED=true
# NEO4J_URI=bolt://graph-neo4j:7687
# NEO4J_USERNAME=neo4j
# NEO4J_PASSWORD=ChangeMe_123!
# NEO4J_DATABASE=neo4j
```

仅在按 `graphrag_db-deploy/README.md` 部署并确需 GraphRAG 时开启。

### 2.6 Compose 专用变量（端口与网络）

```env
APP_PORT=8080                        # 应用对外端口
VLLM_DOCKER_NETWORK=docker_vllm-network
RAG_DOCKER_NETWORK=ai-stack
GRAPH_DOCKER_NETWORK=graph-stack     # 启用 GraphRAG 时
```

- 三个网络名需与对应子项目的 `.env` / compose 一致（可用 `docker network ls` 核对）。  
- 默认已满足典型部署，只有在自定义 project name 或网络时才需要调整。

---

## 3. 启动命令（生产/测试环境）

### 3.1 启动底座服务

```bash
# EasySearch
cd rag_db-deploy
cp .env.example .env          # 首次
docker compose -f docker-compose.easysearch.yml --env-file .env up -d

# vLLM
cd ../vllm-deploy/docker
docker compose up -d

# 可选：Neo4j / GraphRAG
cd ../../graphrag_db-deploy
cp .env.example .env
docker compose -f docker-compose.neo4j.yml --env-file .env up -d
```

### 3.2 启动应用栈

```bash
cd app/app-deploy
cp .env.example .env          # 首次，之后直接编辑 .env
docker compose up -d --build
```

默认会启动：

- `models-app-redis`（Redis，会话存储）；  
- `models-app`（FastAPI 应用）。

如需小模型 GPU 能力（`/small-model/*`），再执行：

```bash
docker compose --profile small-model-gpu up -d --build
```

> GPU profile 的详细说明见 `README.md`，简化版只需知道：不加 `--profile small-model-gpu` 时不会占用 GPU。

---

## 4. 联通性验证（智能客服）

### 4.1 基本健康检查

```bash
# 应用
curl -s "http://127.0.0.1:${APP_PORT:-8080}/health/"

# vLLM
curl -s "http://127.0.0.1:8000/health"

# EasySearch
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"
```

### 4.2 `/chatbot/chat` 测试

```bash
curl -s -X POST "http://127.0.0.1:${APP_PORT:-8080}/chatbot/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "session_id": "demo-session",
    "query": "你好，请简单自我介绍一下。",
    "enable_rag": false,
    "enable_context": false
  }'
```

期望响应：

- HTTP 200；  
- JSON 中 `answer` 字段为模型返回文本（`used_rag=false`、`context_snippets=[]`）。

如需测试带 RAG 的对话，请先按 `rag_db-deploy/README.md` 完成知识摄入，再将 `enable_rag` 设为 `true`。

### 4.3 `/chatbot/chat/stream` 测试（流式）

```bash
curl -N -X POST "http://127.0.0.1:${APP_PORT:-8080}/chatbot/chat/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "session_id": "demo-session",
    "query": "请用三句话介绍一下你自己。",
    "enable_rag": false,
    "enable_context": false
  }'
```

期望看到 `text/event-stream` 输出，每行形如：

```text
data: {"delta":"...","finished":false}
...
data: {"finished":true}
```

---

## 5. 日常运维常用命令

在 `app/app-deploy` 目录：

```bash
# 查看应用日志
docker compose logs -f models-app

# 重启应用
docker compose restart models-app

# 停止应用栈（不影响 vLLM / EasySearch / Neo4j）
docker compose down
```

---

## 6. 推荐阅读

- 需要完整参数与治理策略时：`app/app-deploy/README.md`。  
- 智能客服链路设计：`framework-guide/智能客服整体实现技术说明.md`。  
- RAG / GraphRAG 细节：`framework-guide/RAG整体实现技术说明.md`。  
- 底座数据库部署：`rag_db-deploy/README.md`、`graphrag_db-deploy/README.md`。  
- 大模型服务部署：`vllm-deploy/README.md`。\n
