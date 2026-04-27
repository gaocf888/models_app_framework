# 应用部署简明版（生产配置与上线流程）

> 本文是 `README.md` 的**精简版**，重点面向「应用配置 + 生产/测试环境部署」，不讨论代码开发细节。  
> 若需要完整说明（包括治理策略、GPU profile 细节、运维检查清单），请阅读同目录 `README.md`。
> 若为局域网/离线环境部署外挂服务（vLLM、EasySearch、MinerU），请阅读：`README-external-services-lan-deploy.md`。
> 值班排障请阅读：`deploy-docs/online-services-oncall-runbook.md`（当前先覆盖智能客服）。

> 文档分工建议：  
> - 以本文件作为“上线执行主线”；  
> - 遇到高级参数、GPU profile 细节、运维表格清单时再跳转 `README.md`；  
> - 不在本文件重复维护离线外挂服务与值班排障长文，分别以对应文档为准。

---

## 0. 前提重要说明
> 实现效果较好的NL2SQL的前提是：要有教完善的知识库知识摄入（因为当前NL2SQL对表结构、字段、表间关系的认知，是通过RAG知识库+数据库反射两种方式融合获取的）
    1.  首先RAG知识摄入时，要确保摄入namespace分别为`nl2sql_schema`、`nl2sql_biz_knowledge`、`nl2sql_qa_examples`的三种知识（分别是数据库结构、数据库知识文档、数据库知识问答对（问法 → 标准 SQL））
    2.  app/app-deploy/.env中配置业务数据库的连接信息

## 1. 组件与依赖概览

上线智能客服 / 通用 LLM API 时，通常需要以下容器：

| 能力 | 目录 | 说明 |
|------|------|------|
| 大模型推理（vLLM） | `vllm-deploy/` | 提供 OpenAI 兼容 `/v1/chat/completions` |
| RAG 向量 + 全文库 | `rag_db-deploy/` | EasySearch，存储知识库文档 |
| PDF 扫描解析（可选） | `mineru-deploy/` | 提供 `mineru-api`（扫描件 PDF 转 Markdown） |
| 图数据库（可选 GraphRAG） | `graphrag_db-deploy/` | Neo4j，当前聊天默认仍以向量 RAG 为主 |
| 应用 API | `app/app-deploy/` | FastAPI 服务，暴露 `/chatbot/*`、`/llm/*`、`/analysis/*` 等 |
| 会话存储 | `app/app-deploy/` 内置 Redis | 存储会话历史，可通过 `REDIS_URL` 切换到外部 Redis |

部署顺序推荐：**EasySearch → vLLM →（可选）MinerU →（可选 Neo4j）→ 应用栈**。

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

### 2.3.1 智能客服 LangGraph（建议显式配置）

```env
CHATBOT_GRAPH_ENABLED=true
CHATBOT_INTENT_ENABLED=true
CHATBOT_INTENT_OUTPUT_LABELS=kb_qa,clarify,data_query
CHATBOT_NL2SQL_ROUTE_ENABLED=true
CHATBOT_PROMPT_DEFAULT_VERSION=boiler_v1
CHATBOT_SUGGESTED_QUESTIONS_ENABLED=true
CHATBOT_SUGGESTED_QUESTIONS_MAX=5
CHATBOT_CRAG_ENABLED=true
CHATBOT_CRAG_MAX_ATTEMPTS=2
CHATBOT_CRAG_MIN_SCORE=0.55
CHATBOT_RAG_ENGINE_MODE=agentic
CHATBOT_RAG_ENGINE_FALLBACK=hybrid
CHATBOT_HISTORY_LIMIT=20
CHATBOT_PERSIST_PARTIAL_ON_DISCONNECT=true
CHATBOT_FALLBACK_LEGACY_ON_ERROR=true
MAX_REWRITE_QUERY_LENGTH=256
MAX_GRAPH_LATENCY_MS=60000
CHATBOT_CHECKPOINT_BACKEND=none
CHATBOT_CHECKPOINT_NAMESPACE=chatbot_graph
```

说明：`CHATBOT_HISTORY_LIMIT` 用于“每轮读取历史窗口”，`CONV_MAX_HISTORY_MESSAGES` 用于“会话总保留上限”。

### 2.3.2 Service API Key（调用应用业务 HTTP 接口）

对方后台访问 **`/chatbot`、`/llm`、`/analysis`、`/nl2sql`、`/rag`、`/dajia`** 等路由时，请求头必须带：

`Authorization: Bearer <密钥>`

在 `.env` 中配置其一即可：

```env
# 推荐：可多钥并存（英文逗号），轮换时新旧一起配，再逐步下线旧钥
SERVICE_API_KEYS=your_first_random_secret,your_second_random_secret
# 或仅单钥：
# SERVICE_API_KEY=your_single_random_secret
```

**生成密钥**：应用不提供在线发钥接口。在**仓库根目录**执行（需将仓库根加入 `PYTHONPATH`，以便 `import app`）：

```bash
# Linux / macOS
PYTHONPATH=. python -c "from app.auth.keygen import generate_service_api_key; print(generate_service_api_key())"
```

```powershell
# Windows PowerShell
$env:PYTHONPATH = (Get-Location).Path
python -c "from app.auth.keygen import generate_service_api_key; print(generate_service_api_key())"
```

将打印出的字符串写入 `SERVICE_API_KEYS`（或密钥平台注入同名环境变量），**勿提交真实密钥到 Git**。实现与更多说明见源码 **`app/auth/keygen.py`**；与 vLLM 的 `LLM_DEFAULT_API_KEY` 无关。认证模型、HTTP 状态与安全运维见 **`docs/Service-API-Key-认证与安全说明.md`**。

### 2.4 业务数据库（NL2SQL，可选）

```env
DB_PORT=3306
DB_URL=mysql+aiomysql://root:your_mysql_password@host.docker.internal:${DB_PORT}/aishare
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
APP_PORT=8083                        # 应用对外端口
VLLM_DOCKER_NETWORK=docker_vllm-network
RAG_DOCKER_NETWORK=ai-stack
MINERU_DOCKER_NETWORK=mineru-stack   # 启用 MinerU 时必须存在
GRAPH_DOCKER_NETWORK=graph-stack     # 启用 GraphRAG 时
EMBEDDING_MODELS_HOST_PATH=/aidata/models/embeddings
RERANKER_MODELS_HOST_PATH=/aidata/models/reranker
```

- 网络名需与对应子项目的 `.env` / compose 一致（可用 `docker network ls` 核对）。  
- 两个模型路径变量分别作为嵌入/重排模型根目录，compose 会自动拼接子目录 `bge-small-zh-v1.5` 与 `bge-reranker-large`。

### 2.7 MinerU（可选，扫描件 PDF 建议开启）

当你已部署 `mineru-deploy`，并希望对扫描件 PDF 使用 OCR 解析时，建议在 `.env` 配置：

```env
MINERU_ENABLED=true
MINERU_BASE_URL=http://mineru-api:8000
MINERU_MAX_CONCURRENT=1
MINERU_IO_CONTAINER_PATH=/workspace/mineru-io
MINERU_FORMULA_ENABLE=true
MINERU_TABLE_ENABLE=true
MINERU_PAGE_BATCH_SIZE=50
```

说明：
- `MINERU_BASE_URL` 必须是容器间地址 `http://mineru-api:8000`，不要写宿主机映射端口（如 8009）。  
- `MINERU_IO_CONTAINER_PATH` 需与 compose 挂载 `/workspace/mineru-io` 对齐，并与 `mineru-deploy` 使用同一宿主机 `MINERU_IO_HOST_PATH`。  
- 若暂不使用 MinerU，可保留 `MINERU_ENABLED=false`。

### 2.8 应用日志策略（stdout + 文件轮转）

应用默认会将日志输出到 stdout（可通过 `docker logs` 查看）。  
从当前版本开始，应用支持**额外**写入容器内文件，并按大小轮转/归档压缩：

```env
LOG_FILE_ENABLED=true
LOG_FILE=/workspace/logs/app.log
LOG_FILE_MAX_BYTES=104857600
LOG_FILE_BACKUP_COUNT=10
LOG_FILE_COMPRESS=true
```

说明：

- `LOG_FILE_ENABLED=false` 时，仅 stdout。  
- `LOG_FILE_ENABLED=true` 时，stdout + 文件双写。  
- 轮转触发后会生成 `app.log.1.gz`、`app.log.2.gz` ...（当 `LOG_FILE_COMPRESS=true`）。  
- compose 已挂载 `/workspace/logs` 到命名卷 `app-logs`，容器重建后日志仍可保留。

### 2.9 模型离线使用
> 整个项目中包括 嵌入模型、重排序模型、mineru模型
> 嵌入模型：RAG知识文档切块后转向量(本地离线路径下/aidata/models/embeddings/中存在离线模型文件时，自动走离线，否则自动走在线下载)
> 重排序模型：RAG混合检索多路召回后，进行重排序(默认走在线下载，若走离线：首先需要修改.env中的RAG_RERANKER_MODEL_PATH（放开注释），然后离线下载模型到宿主机/aidata/models/reranker/路径中，注意：如果配置离线了，离线路径中没有有效模型文件，会报错，不会自动切换在线下载)
> 若部署在多卡环境且重排耗时高，建议新增 `.env`：`RAG_RERANKER_DEVICE=cuda:1`（与 vLLM 分卡）。
> mineru模型：使用mineru进行扫描图片格式PDF文件解析

若部署环境**无法访问 Hugging Face Hub**，或希望避免在线下载，推荐将嵌入模型和重排序模型预先下载到宿主机统一离线路径，并通过挂载暴露给应用：
> 嵌入模型和重排序模型离线下载方法：魔塔社区中搜索模型名称，然后使用git lfs下载到下述路径中

1. **在项目根目录准备离线模型目录**

   建议目录结构如下（宿主机）：

   下面是嵌入模型路径
   ```text
   /aidata/models/
     embeddings/
       bge-small-zh-v1.5/   # BAAI/bge-small-zh-v1.5 的完整模型文件
   ```
   
   下面是重排序模型路径
   ```text
   /aidata/models/
     reranker/
       bge-reranker-large/  # BAAI/bge-reranker-large 的完整模型文件
   ```
   
   下面是mineru模型下载路径
   ```text
   /aidata/mineru/models
   ```
   
   mineru模型下载说明
   ```text
   mineru使用在线模式时，魔塔社区(modelscope)下载的模型默认存放路径: ~/.cache/modelscope/hub/models
   若使用离线模式，具体步骤如下：
   1. .env 中修改配置项  MINERU_MODEL_SOURCE=local
                        HF_HUB_OFFLINE=1
                        TRANSFORMERS_OFFLINE=1
   2. 在魔塔社区中搜索OpenDataLab/PDF-Extract-Kit-1.0并使用 git lfs下载到 /data/mineru/models路径下
        为保证下载后路径一致，建议先在有网环境部署，然后使用docker cp从容器中复制下载后的模型到本地，然后拷贝到离线服务器的${MINERU_MODELS_HOST_PATH}路径中（docker cp mineru-api:/root/.cache/modelscope/hub/models/OpenDataLab /data/mineru/models/OpenDataLab）
       下载后路径要确保下面的路径：
         宿主：${MINERU_MODELS_HOST_PATH}/OpenDataLab/PDF-Extract-Kit-1.0/...
         容器：/models/OpenDataLab/PDF-Extract-Kit-1.0/...
   3. docker-compose中已经配置了这些模型文件的挂载(挂载到容器中的 /models路径下)，mineru在上述 MINERU_MODEL_SOURCE=local 配置下，会自动去 /models路径下寻找模型文件
   ```
   

2. **在 compose 中挂载到应用容器**

   在 `app/app-deploy/docker-compose.yml` 中，为应用容器增加只读挂载（示例）：

   ```yaml
   services:
     models-app:
       # ...
       volumes:
         - ${EMBEDDING_MODELS_HOST_PATH:-/aidata/models/embeddings}/bge-small-zh-v1.5:/workspace/models/embeddings/bge-small-zh-v1.5:ro
         - ${RERANKER_MODELS_HOST_PATH:-/aidata/models/reranker}/bge-reranker-large:/models/rerank/bge-reranker-large:ro
       environment:
         - RAG_RERANKER_MODEL_PATH=/models/rerank/bge-reranker-large

     models-app-gpu:
       # ...
       volumes:
         - ${EMBEDDING_MODELS_HOST_PATH:-/aidata/models/embeddings}/bge-small-zh-v1.5:/workspace/models/embeddings/bge-small-zh-v1.5:ro
         - ${RERANKER_MODELS_HOST_PATH:-/aidata/models/reranker}/bge-reranker-large:/models/rerank/bge-reranker-large:ro
       environment:
         - RAG_RERANKER_MODEL_PATH=/models/rerank/bge-reranker-large
   ```

3. **在 `.env` 中指定嵌入/重排模型运行参数**

   在 `app/app-deploy/.env` 中确认或新增：

   ```env
   EMBEDDING_MODEL_PATH=/workspace/models/embeddings/bge-small-zh-v1.5
   RAG_RERANKER_MODEL_PATH=/models/rerank/bge-reranker-large
   # 可选：显式指定重排设备（cpu / cuda / cuda:1）
   # RAG_RERANKER_DEVICE=cuda:1
   ```

   应用启动后，`EmbeddingService` 会**直接从该本地路径加载模型**，不再尝试访问 Hugging Face；`RAGService` 会按 `RAG_RERANKER_DEVICE`（若配置）在指定设备执行重排。

4. **启动/重启应用栈**

   ```bash
   cd app/app-deploy
   docker compose up -d --build
   ```

如果未来更换嵌入模型或重排序模型，只需：

- 在宿主机 `${EMBEDDING_MODELS_HOST_PATH}` / `${RERANKER_MODELS_HOST_PATH}` 下新增对应子目录并放入新模型文件；  
- 必要时调整 `docker-compose.yml` 对应挂载路径；  
- 将 `.env` 中的 `EMBEDDING_MODEL_PATH` / `RAG_RERANKER_MODEL_PATH` 改为新的容器内路径。

---

## 3. 启动命令（生产/测试环境）

### 3.1 启动底座服务

```bash
# EasySearch
cd rag_db-deploy
cp .env.example .env          # 首次
docker compose -f docker-compose.easysearch.yml --env-file .env up -d

# vLLM
cd ../vllm-deploy
chmod +x deploy.sh
./deploy.sh

# 可选：MinerU（扫描件 PDF 解析）
cd ../../mineru-deploy
cp .env.example .env          # 首次
# 如 app 使用 external 网络 mineru-stack，但该网络尚不存在，可先手动创建一次
docker network create mineru-stack || true
docker compose --env-file .env -f docker-compose.cpu.yml up -d

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

# 上面是通用启动方式，若启动app-deploy/时想使用沐曦AI框架镜像(为了reranker效率)，使用下面的启动配置文件
cd app/app-deploy
cp .env.example .env          # 首次，之后直接编辑 .env
cp .env docker-mx
cd docker-mx
docker compose --env-file .env -f docker-compose-mx.yml up -d --build
```
> 启动之前关键修改确认项：LLM_DEFAULT_MODEL  （需要与vllm-deploy/实际部署的大模型名称一致）

默认会启动：

- `models-app-redis`（Redis，会话存储）；  
- `models-app-minio` (MinIO对象存储)
- `models-app`（FastAPI 应用）。

如需小模型 GPU 能力（`/small-model/*`），再执行：

```bash
docker compose --profile small-model-gpu up -d --build
```

> GPU profile 的详细说明见 `README.md`，简化版只需知道：不加 `--profile small-model-gpu` 时不会占用 GPU。

---

## 4. 联通性验证（智能客服）

建议先按 `deploy-docs/online-services-oncall-runbook.md` 执行“5 分钟快速检查”，本节用于部署后补充验证。

### 4.1 基本健康检查

```bash
# 应用
curl -s "http://127.0.0.1:${APP_PORT:-8083}/health/"

# 指标
curl -s "http://127.0.0.1:${APP_PORT:-8083}/metrics" | head

# vLLM
curl -s "http://127.0.0.1:8000/health"

# EasySearch
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"
```

### 4.2 `/chatbot/chat` 测试（兼容接口）

```bash
curl -s -X POST "http://127.0.0.1:${APP_PORT:-8083}/chatbot/chat" \
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

### 4.3 `/chatbot/chat/stream` 测试（流式主用）

```bash
curl -N -X POST "http://127.0.0.1:${APP_PORT:-8083}/chatbot/chat/stream" \
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
data: {"finished":true,"meta":{"status":"answered","intent_label":"kb_qa","retrieval_attempts":1}}
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
