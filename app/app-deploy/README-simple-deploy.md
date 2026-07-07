# 应用部署简明版（生产配置与上线流程）

> 本文是 `README.md` 的**精简版**，重点面向「应用配置 + 生产/测试环境部署」，不讨论代码开发细节。  
> 若需要完整说明（包括治理策略、GPU profile 细节、运维检查清单），请阅读同目录 `README.md`。
> 若为局域网/离线环境部署外挂服务（vLLM、EasySearch、MinerU、**检修 V0 版面侧车 paddleocr-layout-deploy**），请阅读：`README-external-services-lan-deploy.md`。
> 值班排障请阅读：`deploy-docs/online-services-oncall-runbook.md`（当前先覆盖智能客服）。

> 文档分工建议：  
> - 以本文件作为“上线执行主线”；  
> - **嵌入/重排 GPU 部署**（Qwen3 + 英伟达/沐曦）：见 `README.md`「部署形态选择」；  
> - 遇到高级参数、GPU profile 细节、运维表格清单时再跳转 `README.md`；  
> - 不在本文件重复维护离线外挂服务与值班排障长文，分别以对应文档为准。

---

## 0. 前提重要说明
> 实现效果较好的NL2SQL的前提是：要有教完善的知识库知识摄入（因为当前NL2SQL对表结构、字段、表间关系的认知，是通过RAG知识库+数据库反射两种方式融合获取的）
    1.  首先RAG知识摄入时，要确保摄入namespace分别为`nl2sql_schema`、`nl2sql_biz_knowledge`、`nl2sql_qa_examples`的三种知识（分别是数据库结构、数据库知识文档、数据库知识问答对（问法 → 标准 SQL））
    2.  app/app-deploy/.env中配置业务数据库的连接信息
    3.  目前.env中配置了数据库表的白名单（应用进程 · 业务库 - ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT、ANALYSIS_NL2SQL_JOIN_WHITELIST），后续数据库结构更新后，除了更新RAG知识库，无比要同步实现该白名单的更新

## 1. 组件与依赖概览

上线智能客服 / 综合分析 / 检修报告结构化提取（现网 **`/inspection-extract`** 与可选 V0 **`/inspection-extract-v0`**）/ 通用 LLM API 时，通常需要以下容器：

| 能力 | 目录 | 说明 |
|------|------|------|
| 大模型推理（vLLM） | `vllm-deploy/` | 提供 OpenAI 兼容 `/v1/chat/completions` |
| RAG 向量 + 全文库 | `rag_db-deploy/` | EasySearch，存储知识库文档 |
| PDF 扫描解析（可选） | `mineru-deploy/` | 提供 `mineru-api`（扫描件 PDF 转 Markdown） |
| **版面 OCR 侧车（可选，检修 V0）** | **`paddleocr-layout-deploy/`** | **`paddleocr-layout-api`**；主应用 **`INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT`**；须与 **`models-app`** 同 Docker 网络（`PADDLE_LAYOUT_DOCKER_NETWORK`） |
| 图数据库（可选 GraphRAG） | `graphrag_db-deploy/` | Neo4j，当前聊天默认仍以向量 RAG 为主 |
| 应用 API | `app/app-deploy/` | FastAPI 服务，暴露 `/chatbot/*`、`/llm/*`、`/analysis/*`、`/inspection-extract/*`、**`/inspection-extract-v0/*`** 等 |
| 会话存储 | `app/app-deploy/` 内置 Redis | 存储会话历史，可通过 `REDIS_URL` 切换到外部 Redis |

部署顺序推荐：**EasySearch → vLLM →（可选）MinerU →（可选）`paddleocr-layout-deploy` →（可选 Neo4j）→ 应用栈**。

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
CHATBOT_INTENT_BACKEND=rules
# 启用轻量意图 LLM 时：CHATBOT_INTENT_BACKEND=llm + docs/智能客服意图识别轻量LLM接入说明.md
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
CHATBOT_PLANT_KB_ENABLED=true
CHATBOT_PLANT_KB_NAMESPACE=Power_plant_knowledge
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
INTENT_MODELS_HOST_PATH=/aidata/models/intent   # 仅 CHATBOT_INTENT_BACKEND=bert 时需要
INTENT_LLM_MODELS_HOST_PATH=/aidata/models/llm   # 仅 CHATBOT_INTENT_BACKEND=llm 时需要
```

- 网络名需与对应子项目的 `.env` / compose 一致（可用 `docker network ls` 核对）。  
- 模型路径变量作为宿主机根目录，compose 会自动拼接子目录：**`Qwen3-Embedding-0.6B`**（嵌入）、**`Qwen3-Reranker-0.6B`**（重排）、`chatbot-intent-bert`（BERT 意图，可选）、`qwen2.5-0.5b-instruct`（轻量意图 LLM，可选）。

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
- compose 将 `/workspace/logs` bind 到 **app-deploy/logs**（`docker-compose.yml` 为 `./logs`，`docker-mx/docker-compose-mx.yml` 为 `../logs`，宿主机目录一致），容器重建后日志仍保留在宿主机。

### 2.9 模型离线使用（Qwen3 嵌入/重排）

> 当前默认：**Qwen3-Embedding-0.6B**（1024 维）+ **Qwen3-Reranker-0.6B**。  
> 整个项目中还包括：智能客服 BERT 意图（可选）、轻量意图 LLM（可选）、MinerU 模型等。

| 模型 | 作用 | 离线/在线 |
|------|------|-----------|
| **嵌入** | RAG 切块转向量 | 宿主机 `${EMBEDDING_MODELS_HOST_PATH}/Qwen3-Embedding-0.6B` 存在完整 HF 目录时走离线；否则 Hub 下载 |
| **重排** | 混合检索后精排 | 同上 `${RERANKER_MODELS_HOST_PATH}/Qwen3-Reranker-0.6B`；compose 已挂载并设置 `RAG_RERANKER_MODEL_PATH` |
| BERT 意图 | `CHATBOT_INTENT_BACKEND=bert` | 须微调模型，暂不推荐 |
| 轻量意图 LLM | `CHATBOT_INTENT_BACKEND=llm` | 见 `docs/智能客服意图识别轻量LLM接入说明.md` |
| MinerU | 扫描 PDF | 见下文 mineru 小节 |

**Qwen3 专用 `.env`（GPU 栈生效；CPU 栈下 `EMBEDDING_DEVICE` / `RAG_RERANKER_DEVICE` 无效）**：

```env
EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-0.6B
EMBEDDING_MODEL_PATH=/workspace/models/embeddings/Qwen3-Embedding-0.6B
EMBEDDING_QUERY_PROMPT_NAME=query
EMBEDDING_TRUST_REMOTE_CODE=true
EMBEDDING_DEVICE=cuda:0

RAG_RERANKER_MODEL_NAME=Qwen/Qwen3-Reranker-0.6B
RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
RAG_RERANKER_TRUST_REMOTE_CODE=true
RAG_RERANKER_DEVICE=cuda:1
```

**从 BGE 升级**：向量维度 512→1024，须递增 `RAG_ES_INDEX_VERSION` 并 **全量 re-ingest**；FAQ 软直通阈值 `CHATBOT_FAQ_SOFT_DIRECT_MIN_SCORE` 可能需重调（Qwen rerank 为 logit 分）。

若部署环境**无法访问 Hugging Face Hub**，推荐预下载到宿主机并挂载（compose 三栈均已预置 Qwen3 挂载）：
> 嵌入模型和重排序模型离线下载方法：魔塔社区中搜索模型名称，然后使用 git lfs 下载到下述路径中。  
> ollama的模型(qwen2.5-0.5-instract)需要从huggingface下载（git clone https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct /aidata/models/llm/qwen2.5-0.5b-instruct）
> **例外**：BERT 意图模型**不适用**「直接魔塔下载通用预训练 BERT」的方式，须使用已微调三分类模型（见下文说明）。

1. **在项目根目录准备离线模型目录**

   建议目录结构如下（宿主机）：

   下面是嵌入模型路径
   ```text
   /aidata/models/
     embeddings/
       Qwen3-Embedding-0.6B/   # Qwen/Qwen3-Embedding-0.6B 完整 HF 目录
   ```
   
   下面是重排序模型路径
   ```text
   /aidata/models/
     reranker/
       Qwen3-Reranker-0.6B/    # Qwen/Qwen3-Reranker-0.6B 完整 HF 目录
   ```

   离线下载示例：

   ```bash
   mkdir -p /aidata/models/embeddings /aidata/models/reranker
   # huggingface-cli download Qwen/Qwen3-Embedding-0.6B --local-dir /aidata/models/embeddings/Qwen3-Embedding-0.6B
   # huggingface-cli download Qwen/Qwen3-Reranker-0.6B --local-dir /aidata/models/reranker/Qwen3-Reranker-0.6B
   ```

   下面是智能客服 BERT 意图模型路径（仅 `CHATBOT_INTENT_BACKEND=bert` 时需要；默认 `rules` 可跳过）
   ```text
   /aidata/models/
     intent/
       chatbot-intent-bert/  # 微调后的 HF 序列分类模型（非通用预训练 BERT）
         config.json         # 含 id2label：kb_qa / data_query / clarify
         pytorch_model.bin   # 或 model.safetensors
         tokenizer.json
         vocab.txt           # 以及 tokenizer 相关文件
   ```
   **模型要求（必读）**：
   - **可以上线**：自训或第三方交付的、标签为 `kb_qa` / `data_query` / `clarify` 的 HF 序列分类导出目录；
   - **不可直接上线**：魔塔 / HuggingFace 的通用预训练 BERT（`bert-base-chinese`、`hfl/chinese-bert-wwm` 等）— 无业务分类头，挂载后输出无意义；
   - **不想训练**：保持 `CHATBOT_INTENT_BACKEND=rules`，无需准备本目录。

   下面是轻量意图 LLM 模型路径（仅 `CHATBOT_INTENT_BACKEND=llm` 时需要，与嵌入模型相同 HF 直挂）
   ```text
   /aidata/models/
     llm/
       qwen2.5-0.5b-instruct/     # Qwen/Qwen2.5-0.5B-Instruct 标准 HF 目录
         config.json
         model.safetensors
         tokenizer.json
   ```
   离线下载（推荐 huggingface-cli，不必魔塔）：
   ```bash
   pip install -U huggingface_hub
   mkdir -p /aidata/models/llm
   huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct \
     --local-dir /aidata/models/llm/qwen2.5-0.5b-instruct
   ```
   应用侧配置：`CHATBOT_INTENT_LLM_MODEL_PATH=/workspace/models/llm/qwen2.5-0.5b-instruct`。离线机房仅需拷贝 `${INTENT_LLM_MODELS_HOST_PATH}/qwen2.5-0.5b-instruct`。

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
   

2. **compose 挂载（已预置，一般无需手改）**

   `docker-compose.yml`、`docker-nvidia/docker-compose-nvidia.yml`、`docker-mx/docker-compose-mx.yml` 均已挂载 Qwen3 子目录。仅当更换模型目录名时需同步改 compose 与 `.env`。

   ```yaml
   # models-app 示例
   volumes:
     - ${EMBEDDING_MODELS_HOST_PATH:-/aidata/models/embeddings}/Qwen3-Embedding-0.6B:/workspace/models/embeddings/Qwen3-Embedding-0.6B:ro
     - ${RERANKER_MODELS_HOST_PATH:-/aidata/models/reranker}/Qwen3-Reranker-0.6B:/workspace/models/rerank/Qwen3-Reranker-0.6B:ro
   environment:
     - RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
   ```

   `models-app-gpu` 的重排路径为 **`/models/rerank/Qwen3-Reranker-0.6B`**（compose 会覆盖 `.env` 中同名变量）。

3. **在 `.env` 中确认 Qwen3 与索引版本**

   ```env
   EMBEDDING_MODEL_PATH=/workspace/models/embeddings/Qwen3-Embedding-0.6B
   EMBEDDING_QUERY_PROMPT_NAME=query
   EMBEDDING_TRUST_REMOTE_CODE=true
   RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
   RAG_RERANKER_TRUST_REMOTE_CODE=true
   # GPU 栈（docker-nvidia / docker-mx）：
   # EMBEDDING_DEVICE=cuda:0
   # RAG_RERANKER_DEVICE=cuda:1

   # 换嵌入模型后须升版本并 re-ingest
   RAG_ES_INDEX_VERSION=3
   RAG_ES_AUTO_MIGRATE_ON_START=true

   CHATBOT_INTENT_BACKEND=rules
   INTENT_MODELS_HOST_PATH=/aidata/models/intent
   ```

4. **启动/重启应用栈**

   见 [§3.2 启动应用栈](#32-启动应用栈)（按 CPU / 英伟达 GPU / 沐曦 GPU 选择 compose）。

更换嵌入或重排模型时：更新宿主机模型目录 → 同步 compose 挂载子目录名 → 递增 `RAG_ES_INDEX_VERSION` 并 re-ingest。

---

## 3. 启动命令（生产/测试环境）

### 3.1 启动底座服务

```bash
# EasySearch
cd rag_db-deploy
cp .env.example .env          # 首次
docker compose -f docker-compose.easysearch_bak0.yml --env-file .env up -d

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

目前主应用部署，基于算力类型
按算力选择 **一种** compose 栈（详见 `README.md`「部署形态选择」）：

```bash
cd app/app-deploy
cp .env.example .env          # 首次，之后直接编辑 .env
```

**CPU（默认）** — 嵌入/重排跑 CPU，`.env` 中 `cuda:N` 不生效：

```bash
docker compose up -d --build
```

**英伟达 GPU** — Qwen3 嵌入/重排可用 GPU（需宿主机 NVIDIA Container Toolkit）：

```bash
docker compose -f docker-nvidia/docker-compose-nvidia.yml up -d --build
# 可选小模型 GPU：
# docker compose -f docker-nvidia/docker-compose-nvidia.yml --profile small-model-gpu up -d models-app-gpu
```

**沐曦 GPU** — Metax 镜像栈：

```bash
cp .env docker-mx/            # 或 docker-mx 使用 ../.env
cd docker-mx
docker compose --env-file .env -f docker-compose-mx.yml up -d --build
```

> 启动前确认：**`LLM_DEFAULT_MODEL`** 与 `vllm-deploy/` 实际部署的大模型名称一致。

默认会启动：

- `models-app-redis`（Redis，会话存储）；  
- `models-app-minio` (MinIO对象存储)
- `models-app`（FastAPI 应用）。

如需小模型 GPU 能力（`/small-model/*`），再执行：

```bash
docker compose --profile small-model-gpu up -d --build
```

> GPU profile 的详细说明见 `README.md`，简化版只需知道：不加 `--profile small-model-gpu` 时不会占用 GPU。

### 3.1 人脸识别（InsightFace，`/face/*`）

**主镜像 `models-app`（端口 `${APP_PORT:-8083}`）已内置 CPU 版 InsightFace**，无需 `--profile small-model-gpu` 即可使用人脸库录入与 `/face/identify` 等 API。

视频流通道（`/small-model/channel/start` + `algor_type=431xx`）仍需 **small-model-gpu** profile（含解码线程 + YOLO 等完整小模型栈）；若仅做人脸库管理与单图识别，用主端口即可。

容器内人脸库卷：`face-galleries-data` → `/workspace/data/face_galleries`。

```bash
# 1) 创建库（主端口 8083，无需 GPU profile）
curl -X POST "http://127.0.0.1:${APP_PORT:-8083}/face/gallery" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${SERVICE_API_KEY}" \
  -d '{"gallery_id":"default","name":"默认库"}'

# 2) 录入
curl -X POST "http://127.0.0.1:${APP_PORT:-8083}/face/gallery/default/enroll" \
  -H "X-API-Key: ${SERVICE_API_KEY}" \
  -F "person_id=emp001" -F "name=张三" -F "file=@/path/to/face.jpg"

# 3) 单图识别（可选 face_alert_mode=unknown 仅陌生人）
curl -X POST "http://127.0.0.1:${APP_PORT:-8083}/face/identify" \
  -H "X-API-Key: ${SERVICE_API_KEY}" \
  -F "gallery_id=default" -F "face_alert_mode=both" -F "file=@/path/to/scene.jpg"

# 4) 视频通道（需 GPU profile，43102=白名单+陌生人，43103=仅陌生人，43104=ROI）
curl -X POST "http://127.0.0.1:${APP_PORT_GPU:-8081}/small-model/channel/start" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${SERVICE_API_KEY}" \
  -d '{
    "channel_id": "cam01",
    "algor_type": "43102",
    "gallery_id": "default",
    "video_source": "rtsp://user:pass@192.168.1.10/stream1"
  }'
```

回调 payload 含 `alert_types`（`identified` / `unknown`）、`face_alerts`（实际触发告警的人脸列表）；陌生人与白名单使用独立冷却键（`unknown_cooldown_seconds`）。

生产建议预下载 InsightFace 模型并设置 `INSIGHTFACE_MODELS_HOST_PATH`（见 `.env.example`）。

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

### 4.1.1 `/inspection-extract/upload` + `/inspection-extract/run` 测试（检修报告结构化提取）

推荐调用顺序：先 upload，再 run。

```bash
# 1) 上传文档（返回 url 与 source_type）
curl -s -X POST "http://127.0.0.1:${APP_PORT:-8083}/inspection-extract/upload" \
  -H "Authorization: Bearer ${SERVICE_API_KEY}" \
  -F "file=@/path/to/report.docx"

# 2) 使用上一步返回的 url 调用 run
curl -s -X POST "http://127.0.0.1:${APP_PORT:-8083}/inspection-extract/run" \
  -H "Authorization: Bearer ${SERVICE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "session_id": "demo-session",
    "source_type": "docx",
    "content": "https://minio.xxx/presigned-url",
    "strict": false,
    "return_evidence": true
  }'
```

期望响应：

- HTTP 200；
- `records` 返回结构化结果；
- `summary` 包含 `total/defect_count/replace_count`；
- `trace.parse_route` 可见 `docx`、`pdf_text` 或 `mineru`。

### 4.1.2 检修 V0（`/inspection-extract-v0/*`，可选）

前置：**`INSPECT_EXTRACT_V0_ENABLED=true`**；版面 OCR 需 **`paddleocr-layout-deploy`** 已启动且与主应用同网（见 **`enterprise-level_transformation_docs/企业级检修报告结构化提取V0版本实现和使用说明.md`**）。路由与现网镜像：`upload` → `run` 或 `run/async` → `GET jobs/{id}`。

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

## 5. 日常运维

> 端口以各目录 `.env` 为准；下表为**默认值**。智能客服值班排障见 `deploy-docs/online-services-oncall-runbook.md`。

### 5.1 组件访问一览

| 组件 | 容器名（默认） | 宿主机访问（浏览器 / curl） | 容器内访问（应用配置用） | 说明 |
|------|----------------|----------------------------|--------------------------|------|
| **应用 API** | `models-app` | `http://<host>:${APP_PORT:-8083}` | — | 健康检查：`/health/`；业务接口需 `Authorization: Bearer <SERVICE_API_KEY>` |
| **应用 API（GPU 小模型）** | `models-app-gpu` | `http://<host>:${APP_PORT_GPU:-8081}` | — | 需 `--profile small-model-gpu` 启动 |
| **Redis** | `models-app-redis` | 一般不对外暴露 | `redis://redis:6379/0` | 会话历史；无 Web 界面 |
| **MinIO API** | `models-app-minio` | `http://<host>:${MINIO_PORT:-9000}` | `models-app-minio:9000` | S3 协议；应用上传/预签名走此端口 |
| **MinIO Console** | `models-app-minio` | **`http://<host>:${MINIO_CONSOLE_PORT:-9001}`** | — | **自带 Web 管理界面**；登录见下表 |
| **vLLM** | `vllm-service` | `http://<host>:8000/health` | `http://vllm-service:8000/v1` | 大模型推理；`vllm-deploy/` 部署 |
| **EasySearch（RAG）** | `rag-easysearch` | `https://<host>:9200` | `https://rag-easysearch:9200` | 知识库向量+全文；账号 `RAG_ES_USERNAME` / `RAG_ES_PASSWORD` |
| **MinerU（可选）** | `mineru-api` | `http://<host>:${MINERU_PORT:-8009}/health` | `http://mineru-api:8000` | 扫描 PDF 解析；API 文档 `/docs` |
| **Neo4j（可选）** | `graph-neo4j` | `http://<host>:7474` | `bolt://graph-neo4j:7687` | GraphRAG；`graphrag_db-deploy/` |
| **版面 OCR 侧车（可选）** | `paddleocr-layout-api` | `http://<host>:8010/health` | `http://paddleocr-layout-api:8000` | 检修 V0；`paddleocr-layout-deploy/` |

**MinIO 控制台登录**（`.env` 中 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`，示例默认 `minioadmin` / `minioadmin123`）：

- 常用 bucket：`chatbot-images`（客服图片）、`rag-assets`（RAG 知识库 figure 图，key 前缀亦为 `rag-assets/`）。
- **勿在宿主机挂载目录里直接当普通文件打开图片**：MinIO 落盘为 `对象名/xl.meta` 目录结构，属正常格式；预览/下载请用 **Console（9001）** 或预签名 URL。

### 5.2 快速健康检查

```bash
# 应用
curl -s "http://127.0.0.1:${APP_PORT:-8083}/health/"

# vLLM
curl -s "http://127.0.0.1:8000/health"

# EasySearch
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"

# MinIO（API 存活，需 mc 或 Console 看对象）
curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:${MINIO_PORT:-9000}/minio/health/live"
```

### 5.3 本栈常用命令

在 `app/app-deploy` 目录（沐曦：`docker-mx/` + `docker-compose-mx.yml`；英伟达：`docker-nvidia/docker-compose-nvidia.yml`）：

```bash
# 查看状态
docker compose ps

# 应用日志（stdout + 若开启则含文件日志挂载）
docker compose logs -f models-app

# Redis / MinIO 日志
docker compose logs -f redis minio

# 重启应用（不动 vLLM / EasySearch 等外挂）
docker compose restart models-app

# 停止本栈（不删 vLLM / EasySearch / Neo4j 数据）
docker compose down
```

GPU 实例曾启动时：

```bash
docker compose --profile small-model-gpu logs -f models-app-gpu
docker compose --profile small-model-gpu down
```

### 5.4 持久化目录（宿主机）

| 环境变量（默认） | 用途 |
|------------------|------|
| `REDIS_DATA_HOST_PATH` → `/aidata/data/redis_data` | 会话 Redis 数据 |
| `MINIO_DATA_HOST_PATH` → `/aidata/data/minio_data` | MinIO 对象数据 |
| `SESSION_STORAGE_HOST_PATH` | 会话归档本地目录 |
| `RAG_FAISS_DATA_HOST_PATH` | FAISS 索引（仅 `RAG_VECTOR_STORE_TYPE=faiss`） |
| `app-deploy/logs`（compose 挂载） | 应用轮转日志（`LOG_FILE_ENABLED=true` 时） |

---

## 6. 推荐阅读

- 需要完整参数与治理策略时：`app/app-deploy/README.md`。  
- 智能客服链路设计：`framework-guide/智能客服整体实现技术说明.md`。  
- 检修报告结构化提取（现网）：`framework-guide/报告解析企业级实现方案.md`、`enterprise-level_transformation_docs/企业级检修报告结构化提取实现和使用说明.md`。  
- **检修 V0 + 版面侧车**：`framework-guide/报告解析企业级实现方案V0.md`、`enterprise-level_transformation_docs/企业级检修报告结构化提取V0版本实现和使用说明.md`、`paddleocr-layout-deploy/README.md`。  
- RAG / GraphRAG 细节：`framework-guide/RAG整体实现技术说明.md`。  
- 底座数据库部署：`rag_db-deploy/README.md`、`graphrag_db-deploy/README.md`。  
- 大模型服务部署：`vllm-deploy/README.md`。
