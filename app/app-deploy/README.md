# 应用服务 Docker 部署（在线 API）

本目录提供 **FastAPI 应用层** 的容器化部署，与仓库内 **`vllm-deploy/`**、**`rag_db-deploy/`** 对接。本文档说明**如何配置、如何启动、多种运行形态（CPU / 英伟达 GPU / 沐曦 GPU / 昇腾 Ascend / 小模型 GPU profile）的差异与排错**。

> 局域网/离线部署外挂服务（vLLM、EasySearch、MinerU、**检修 V0 版面侧车 paddleocr-layout-deploy**）请配合阅读：`README-external-services-lan-deploy.md`。  
> 值班排障请配合阅读：`deploy-docs/online-services-oncall-runbook.md`（当前先覆盖智能客服）。

**目录**

- [能力对照与组件](#能力对照与组件)
- [部署形态选择（CPU / 英伟达 GPU / 沐曦 GPU / 昇腾 Ascend）](#部署形态选择cpu--英伟达-gpu--沐曦-gpu--昇腾-ascend)
- [配置分层：谁在读哪些变量](#配置分层谁在读哪些变量)
- [Service API Key（业务 HTTP 鉴权）](#service-api-key业务-http-鉴权)
- [前置条件](#前置条件)
- [第一次部署（默认：仅大模型 + RAG + 会话）](#第一次部署默认仅大模型--rag--会话)
- [启用小模型 GPU profile（完整步骤）](#启用小模型-gpu-profile完整步骤)
- [端口与多实例策略](#端口与多实例策略)
- [卷与持久化策略](#卷与持久化策略)
- [验证与健康检查](#验证与健康检查)
- [日常运维命令](#日常运维命令)
- [在线业务与离线训练（概念说明）](#在线业务与离线训练概念说明)
- [网络与 DNS](#网络与-dns)
- [小模型权重与 YAML 路径约定](#小模型权重与-yaml-路径约定)
- [嵌入与 Hugging Face 缓存（含离线模型目录约定）](#嵌入与-hugging-face-缓存含离线模型目录约定)
- [故障排查](#故障排查)
- [运维检查清单（表格）](#运维检查清单表格)
- [与框架文档的对应](#与框架文档的对应)
- [本目录文件清单](#本目录文件清单)

---

## 文档分工（先看这个）

为减少重复维护，建议按目标阅读：

| 目标 | 优先文档 |
|------|----------|
| 快速上线（最少步骤） | `README-simple-deploy.md` |
| 完整部署与参数说明（本文件） | `README.md` |
| **英伟达 GPU 嵌入/重排（Qwen3）** | **`docker-nvidia/README.md`** + **`docker-nvidia/docker-compose-nvidia.yml`** + 本文「部署形态选择」 |
| **沐曦 GPU 嵌入/重排** | **`docker-mx/docker-compose-mx.yml`** + 本文「部署形态选择」 |
| **昇腾 Ascend 嵌入/重排** | **`docker-ascend/README.md`** + **`docker-ascend/docker-compose-ascend.yml`** + 本文「部署形态选择」 |
| 局域网/离线外挂服务（vLLM/EasySearch/MinerU/**Paddle 版面侧车**） | `README-external-services-lan-deploy.md` |
| 值班排障（当前先覆盖智能客服） | `deploy-docs/online-services-oncall-runbook.md` |

---

## 能力对照与组件

| 能力 | 部署位置 | 说明 |
|------|----------|------|
| 大模型推理（vLLM） | `vllm-deploy/` | OpenAI 兼容 HTTP；应用通过 `LLM_DEFAULT_ENDPOINT` 访问 |
| 向量 + 全文检索 | `rag_db-deploy/`（EasySearch） | 应用通过 `RAG_ES_*` 连接 |
| 扫描 PDF 解析（可选） | `mineru-deploy/` | 提供 `mineru-api`，用于扫描件 PDF 转 Markdown |
| 检修结构化 V0 · 版面 OCR（可选） | **`paddleocr-layout-deploy/`** | 独立 **`paddleocr-layout-api`**；主应用 **`INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT`**；与 **`models-app`** 同 Docker 网络（`PADDLE_LAYOUT_DOCKER_NETWORK` / `paddle-layout-stack`） |
| 会话与上下文 | 本 compose 的 **Redis** | `REDIS_URL` 必须指向本栈中的 `redis` 服务名 |
| 应用 HTTP API | **models-app**（默认）或 **models-app-gpu**（profile） | 见下文端口与 profile 说明 |

持久化与外置依赖的通用约定见：`framework-guide/数据持久化与容器部署说明.md`。

---

## 部署形态选择（CPU / 英伟达 GPU / 沐曦 GPU / 昇腾 Ascend）

RAG **嵌入**与**重排**模型当前默认为 **Qwen3-Embedding-0.6B** + **Qwen3-Reranker-0.6B**（见 `.env.example`）。按宿主机算力与显卡类型选择 compose 栈：

| 形态 | Compose / Dockerfile | 嵌入/重排设备 | 适用场景 |
|------|----------------------|---------------|----------|
| **CPU（默认）** | `docker-compose.yml` + `Dockerfile` | **CPU**（镜像无 CUDA/NPU PyTorch；`.env` 中 `cuda:N` / `npu:N` 不生效） | 开发联调、无加速卡、或 Qwen3 0.6B 可接受 CPU 延迟 |
| **英伟达 GPU** | **`docker-nvidia/docker-compose-nvidia.yml`** + **`Dockerfile-nvidia`** | **GPU**（cu121 + `cuda:0` / `cuda:1`） | 英伟达服务器；与 vLLM 分卡跑嵌入/重排 |
| **沐曦 GPU** | **`docker-mx/docker-compose-mx.yml`** + **`Dockerfile-mx`** | **GPU**（Metax 基础镜像 + conda；常用 `cuda:N` 串） | 沐曦/麒麟等 Metax 栈 |
| **昇腾 Ascend** | **`docker-ascend/docker-compose-ascend.yml`** + **`Dockerfile-ascend`** | **NPU**（`vllm-ascend:v0.23.0-310p` + `npu:0` / `npu:1`） | Atlas 300I Duo；与 vLLM/MinerU 同底座 |
| **小模型 GPU profile** | 英伟达/沐曦 compose + `--profile small-model-gpu` | 同上 + YOLO/通道等小模型 | 需 `/small-model/*`、视频通道等（昇腾默认不含） |

**`.env` 共用**：各主栈均读取 **`app/app-deploy/.env`**（`docker-nvidia` / `docker-ascend` 用 `../.env`；`docker-mx` 常需复制到子目录）。显卡相关注释见 `.env.example`。

**Qwen3 嵌入/重排推荐 `.env` 片段**：

```env
# 英伟达 / 沐曦
# EMBEDDING_DEVICE=cuda:0
# RAG_RERANKER_DEVICE=cuda:1

# 昇腾 Ascend（§5：ASCEND_RT_VISIBLE_DEVICES=4,5 → 容器内相对 npu:0 / npu:1）
EMBEDDING_DEVICE=npu:0
RAG_RERANKER_DEVICE=npu:1
ASCEND_RT_VISIBLE_DEVICES=4,5
BASE_IMAGE=quay.io/ascend/vllm-ascend:v0.23.0-310p
```

> `models-app-gpu` 的重排容器路径为 **`/models/rerank/Qwen3-Reranker-0.6B`**（与 `models-app` 的 `/workspace/models/rerank/...` 不同），compose 已写死挂载，`.env` 中 `RAG_RERANKER_MODEL_PATH` 会被 compose `environment` 覆盖为对应值。

**从 BGE 升级到 Qwen3 时**（向量维度 512→1024）须：

1. 递增 **`RAG_ES_INDEX_VERSION`**（如 `2`→`3`），保持 **`RAG_ES_AUTO_MIGRATE_ON_START=true`** 或调用 `POST /rag/migrations/chunks/run`（body：`{"embedding_dim": 1024}`）；  
2. **全量 re-ingest** 知识库；  
3. 视情况重调 **`CHATBOT_FAQ_SOFT_DIRECT_MIN_SCORE`**（Qwen3 rerank 分数为 logit，非 0~1）。

**启动示例**：

```bash
cd app/app-deploy

# CPU（默认）
docker compose up -d --build

# 英伟达 GPU（嵌入/重排）
docker compose -f docker-nvidia/docker-compose-nvidia.yml up -d --build

# 沐曦 GPU
cp .env docker-mx/   # 或 docker-mx 使用 ../.env
cd docker-mx && docker compose -f docker-compose-mx.yml up -d --build

# 昇腾 Ascend
docker compose -f docker-ascend/docker-compose-ascend.yml up -d --build

# 可选小模型 GPU（英伟达栈示例）
docker compose -f docker-nvidia/docker-compose-nvidia.yml --profile small-model-gpu up -d models-app-gpu
```

---

## 配置分层：谁在读哪些变量

理清这一点可以避免「改了 `.env` 但 compose 不生效」或「应用读不到变量」的问题。

| 层级 | 作用 | 典型变量 |
|------|------|----------|
| **Docker Compose 在宿主机解析** | 用于 `docker-compose.yml` 里的**插值**（镜像、端口、网络名、数据卷源路径）。只在执行 `docker compose` 的 shell 环境 + **本目录 `.env`** 中取值。 | `APP_PORT`、`EMBEDDING_MODELS_HOST_PATH`、`RERANKER_MODELS_HOST_PATH`、`INTENT_MODELS_HOST_PATH`、`INTENT_LLM_MODELS_HOST_PATH`、`SMALL_MODEL_WEIGHTS_HOST_PATH` 等 |
| **注入应用容器的环境变量** | **`env_file: .env`** 把整个 `.env` 打进 **`models-app` / `models-app-gpu` 进程**，由 **`app/core/config.py`** 的 `os.getenv` 读取。应用**不会**自己 `load_dotenv` 读磁盘上的 `.env`。 | `SERVICE_API_KEYS` / `SERVICE_API_KEY`、`LLM_*`、`RAG_*`、`REDIS_URL`、`DB_*`、`GRAPH_*`、`MINERU_*`、`EMBEDDING_*` 等 |

**配置策略建议**

1. **本目录始终维护一份 `.env`**：从 `.env.example` 复制，禁止提交真实密钥到 Git。  
2. **与 EasySearch 账号一致**：`RAG_ES_USERNAME` / `RAG_ES_PASSWORD` 须与 EasySearch 实际 `admin` 密码一致（见 `rag_db-deploy` 说明、容器内 `reset_admin_password.sh`）。  
3. **与 vLLM 模型名一致**：`LLM_DEFAULT_MODEL` 与 vLLM `--served-model-name` 一致。  
4. **容器内访问别用 `127.0.0.1` 指其它容器**：应使用 **`vllm-service`、`rag-easysearch`、`redis`** 等服务名（前提是本栈已加入对应外部网络，见 `docker-compose.yml`）。

---

## Service API Key（业务 HTTP 鉴权）

对方后台调用 **`/chatbot/*`、`/llm/*`、`/analysis/*`、`/nl2sql/*`、`/rag/*`、`/dajia/*`** 等受保护路由时，须在请求头携带：

`Authorization: Bearer <密钥>`

- **配置**：环境变量 **`SERVICE_API_KEYS`**（英文逗号分隔多个密钥，轮换时新旧并存）或单个 **`SERVICE_API_KEY`**。与 `LLM_DEFAULT_API_KEY`（访问 vLLM）无关，勿混用。
- **生成**：不在应用内提供 HTTP 签发接口。在**仓库根目录**用 `app/auth/keygen.py` 中的 **`generate_service_api_key()`**（基于 `secrets.token_urlsafe`）生成本地随机串，写入本目录 `.env` 或 CI/Secret。具体命令见 **`keygen.py` 模块顶部文档字符串**；简明步骤亦见 **`README-simple-deploy.md`** 对应小节。
- **校验**：`app/auth/dependencies.py` 将 Bearer 与配置密钥做常量时间比对；未配置任何密钥时业务路由返回 **503**。
- **安全与行为总述**：`docs/Service-API-Key-认证与安全说明.md`（仓库根目录下 `docs`）。

---

## 前置条件

1. **Docker**、**Docker Compose V2**。  
2. **外部依赖已启动且网络已存在**：  
   - EasySearch：`rag_db-deploy`，默认容器 **`rag-easysearch`**，默认外部网络 **`ai-stack`**。  
   - vLLM：`vllm-deploy`（推荐 `deploy.sh` 启动），默认容器 **`vllm-service`**；外部网络常为 **`docker_vllm-network`**（以实际 `docker network ls` 为准）。  
   - MinerU（可选）：`mineru-deploy`，默认容器 **`mineru-api`**，默认外部网络 **`mineru-stack`**。  
   - 检修 V0 版面侧车（可选）：**`paddleocr-layout-deploy/`**，默认容器 **`paddleocr-layout-api`**，默认外部网络 **`paddle-layout-stack`**（与 **`PADDLE_LAYOUT_NETWORK_NAME`** / **`PADDLE_LAYOUT_DOCKER_NETWORK`** 一致）。  
3. 在宿主机执行 **`docker network ls`**，若名称与上表不一致，在 `.env` 中修改 **`VLLM_DOCKER_NETWORK`**、**`RAG_DOCKER_NETWORK`**、**`MINERU_DOCKER_NETWORK`**、**`PADDLE_LAYOUT_DOCKER_NETWORK`**。  
4. **Linux**：EasySearch 若报 `vm.max_map_count`，按 `rag_db-deploy/README.md` 在宿主机调内核参数。  
5. **小模型 GPU**：宿主机安装 **NVIDIA 驱动**；Docker 使用 **NVIDIA Container Toolkit**；Windows 下 **Docker Desktop（WSL2）** 需在设置中启用 **GPU**，否则 `gpus: all` 无效。

---

## 第一次部署（默认：仅大模型 + RAG + 会话）

> **Compose 选型**：默认 **`docker compose up`** 为 **CPU 栈**（嵌入/重排无 GPU）。英伟达 GPU 用 **`docker-nvidia/docker-compose-nvidia.yml`**，沐曦用 **`docker-mx/docker-compose-mx.yml`** — 见上文 [部署形态选择](#部署形态选择cpu--英伟达-gpu--沐曦-gpu)。  
> **Qwen3 模型**：`.env.example` 已默认 Qwen3 嵌入/重排；离线目录见 [嵌入与 Hugging Face 缓存](#嵌入与-hugging-face-缓存含离线模型目录约定)。

### 步骤 1：准备本目录环境文件

```bash
cd app/app-deploy
cp .env.example .env
```

用编辑器打开 `.env`，**至少**修改：

- `LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1`（若 vLLM 端口或主机名不同，需改成可解析 URL）  
- `LLM_DEFAULT_MODEL=` 与当前 vLLM 模型别名一致  
- `RAG_ES_HOSTS=https://rag-easysearch:9200`（HTTP/HTTPS、`verify` 与库一致）  
- `RAG_ES_USERNAME` / `RAG_ES_PASSWORD`  
- `DB_URL` 或 `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME`（若使用 NL2SQL；数据库在宿主机时用 `host.docker.internal`）

`REDIS_URL=redis://redis:6379/0` 一般**保持默认**（`redis` 为本 compose 服务名）。

建议同时在 `.env` 显式补充智能客服 LangGraph 参数（即便有默认值）：

- `CHATBOT_INTENT_ENABLED=true`
- `CHATBOT_INTENT_BACKEND=rules`（可选 `bert`；**bert 须已微调分类模型，不可用魔塔通用预训练 BERT**；见 `docs/智能客服意图识别BERT接入说明.md`）
- `CHATBOT_INTENT_OUTPUT_LABELS=kb_qa,clarify,data_query,hybrid_qa`
- `CHATBOT_NL2SQL_ROUTE_ENABLED=true`（智能客服内嵌 NL2SQL 分流）
- `CHATBOT_PROMPT_DEFAULT_VERSION=boiler_v1`（默认锅炉领域客服模板）
- `CHATBOT_SUGGESTED_QUESTIONS_ENABLED=true` / `CHATBOT_SUGGESTED_QUESTIONS_MAX=5`
- `CHATBOT_CRAG_ENABLED=true`
- `CHATBOT_CRAG_MAX_ATTEMPTS=2`
- `CHATBOT_CRAG_MIN_SCORE=0.55`
- `CHATBOT_RAG_ENGINE_MODE=agentic`
- `CHATBOT_RAG_ENGINE_FALLBACK=hybrid`
- `CHATBOT_PLANT_KB_ENABLED=true` / `CHATBOT_PLANT_KB_NAMESPACE=Power_plant_knowledge`（本厂指代锁定主 RAG namespace，见企业方案 §16）
- `CHATBOT_HISTORY_LIMIT=20`
- `CHATBOT_PERSIST_PARTIAL_ON_DISCONNECT=true`
- `MAX_REWRITE_QUERY_LENGTH=256`
- `MAX_GRAPH_LATENCY_MS=60000`
- `CHATBOT_CHECKPOINT_BACKEND=none`
- `CHATBOT_CHECKPOINT_NAMESPACE=chatbot_graph`
- **上下文指代（可选）**：`CHATBOT_ANAPHORA_*`（检索融合 P0、锚块 P1、槽位 P2、Coref P3）；说明见 `enterprise-level_transformation_docs/企业级智能客服 LangGraph 框架实现方案.md` **第 15 节**。

说明：对话入口仅 `POST /chatbot/chat/stream`（须安装 `langgraph`）；`CHATBOT_HISTORY_LIMIT` 控制“单轮读取历史条数”，`CONV_MAX_HISTORY_MESSAGES` 控制“会话总保留上限”；建议两者同时配置。

若启用扫描件 PDF 解析，建议同时确认以下变量：

- `MINERU_ENABLED=true`
- `MINERU_BASE_URL=http://mineru-api:8000`（容器间地址，不是宿主机映射端口）
- `MINERU_IO_CONTAINER_PATH=/workspace/mineru-io`（需与 compose 挂载一致）
- `MINERU_MAX_CONCURRENT=1`（生产建议先从 1 开始）
- `MINERU_FORMULA_ENABLE`、`MINERU_TABLE_ENABLE`、`MINERU_PAGE_BATCH_SIZE`（用于 CPU 压力控制）

### 步骤 2：按顺序启动外部依赖

```bash
# 2.1 EasySearch（在仓库 rag_db-deploy 目录，按该目录 README 准备 .env）
cd rag_db-deploy
docker compose -f docker-compose.easysearch_bak0.yml --env-file .env up -d

# 2.2 vLLM
cd ../vllm-deploy
chmod +x deploy.sh
./deploy.sh

# 2.3 MinerU（可选）
cd ../../mineru-deploy
cp .env.example .env                           # 首次
docker network create mineru-stack || true     # external 网络不存在时先创建一次
docker compose --env-file .env -f docker-compose.cpu.yml up -d
```

### 2.4 检修 V0 版面侧车（可选，`paddleocr-layout-deploy`）

启用 **`INSPECT_EXTRACT_V0_ENABLED`** 且 docx/pdf 走版面 OCR 时，需先起侧车，并保证 **`PADDLE_LAYOUT_DOCKER_NETWORK`** 与侧车网络同名（默认 **`paddle-layout-stack`**）。详见 **`paddleocr-layout-deploy/README.md`** 与 **`enterprise-level_transformation_docs/企业级检修报告结构化提取V0版本实现和使用说明.md`**。

```bash
cd ../../paddleocr-layout-deploy
cp .env.example .env
docker network create paddle-layout-stack || true   # 若尚未由本 compose 创建
docker compose -f docker-compose.cpu.yml up -d --build   # 或 docker-compose.yml；沐曦/英伟达见该目录 GPU compose
```

### 步骤 3：启动本栈（Redis + MinIO + models-app）

**CPU（默认）**：

```bash
cd ../../app/app-deploy
docker compose up -d --build
```

**英伟达 GPU**（Qwen3 嵌入/重排 + 可选 `small-model-gpu` profile）：

```bash
cd ../../app/app-deploy
docker compose -f docker-nvidia/docker-compose-nvidia.yml up -d --build
```

**沐曦 GPU**：

```bash
cd ../../app/app-deploy/docker-mx
docker compose -f docker-compose-mx.yml up -d --build
```

### 步骤 4：验证

```bash
curl -s "http://127.0.0.1:8083/health/"
curl -s "http://127.0.0.1:8083/metrics" | head
```

（若修改了 `APP_PORT`，把 `8083` 换成对应宿主机端口。）

---

## 启用小模型 GPU profile（完整步骤）

**含义**：在默认能力之上，再起一个带 **CUDA PyTorch + Ultralytics（YOLO）** 的应用容器 **`models-app-gpu`**，用于 **`/small-model/*`** 等需要 GPU 推理的路径。**不启用 profile 时，该服务不存在，不占 GPU。**

### 步骤 A：宿主机与 Docker GPU

1. 安装 **NVIDIA 驱动**（与 PyTorch cu121 兼容的较新驱动一般即可）。  
2. Linux：安装并配置 **NVIDIA Container Toolkit**，使 `docker run --gpus all` 可用。  
3. Windows：Docker Desktop + WSL2，在 Settings → Resources → GPU 中启用 GPU。

### 步骤 B：准备权重目录（宿主机）

1. 在宿主机创建目录，例如 Linux：`/opt/small-model-weights`，Windows：`F:\weights\small`。  
2. 将 `.pt` 等权重放入该目录，目录结构需与 YAML 中路径能拼到容器内一致（见下文 [小模型权重与 YAML 路径约定](#小模型权重与-yaml-路径约定)）。

### 步骤 C：编辑 `.env`

在已有 `.env` 中确认或增加：

```env
APP_PORT_GPU=8081
SMALL_MODEL_NVIDIA_VISIBLE_DEVICES=all
SMALL_MODEL_WEIGHTS_HOST_PATH=/opt/small-model-weights
```

- **`SMALL_MODEL_WEIGHTS_HOST_PATH`**：宿主机上的**绝对路径**；会只读挂载到容器 **`/workspace/models/small`**。  
  若不设置：Compose 使用命名卷占位（**无真实权重**），仅便于试跑 compose，不适合生产。

### 步骤 D：将算法配置改为使用 GPU（仓库内 YAML）

编辑 **`configs/small_model_algorithms.yaml`**（或你实际使用的算法条目），将 `device` 从 `"cpu"` 改为 **`"0"`** 或 **`"cuda:0"`**（与 Ultralytics 习惯一致）。  
修改后 **`docker compose restart models-app`**（或 `models-app-gpu`）即可；`configs` 已通过宿主机 bind mount 挂载（默认 `/opt/deploy/models_app_framework/configs`）。

### 步骤 E：构建并启动 profile

在 **`app/app-deploy`** 目录：

```bash
docker compose --profile small-model-gpu up -d --build
```

此命令会：

- 照常启动 **Redis**（无 profile 限制）  
- 若未停过，仍会运行默认 **models-app**（`8083`）  
- **额外**启动 **models-app-gpu**（**`8081`**，由 `APP_PORT_GPU` 指定）

### 步骤 F：验证 GPU 容器

```bash
curl -s "http://127.0.0.1:8081/health/"
docker exec models-app-gpu python -c "import torch; print('cuda:', torch.cuda.is_available(), 'count:', torch.cuda.device_count())"
```

期望 **`cuda: True`** 且 **`count >= 1`**。

### 仅保留一个对外 API（全功能单机）

若希望**只对外暴露一个端口**（例如统一 `8083`）且该实例带 GPU：

```bash
docker compose stop models-app
```

在 `.env` 中设置 **`APP_PORT_GPU=8083`**，再执行：

```bash
docker compose --profile small-model-gpu up -d --build
```

此时仅 **`models-app-gpu`** 监听宿主 `8083`（勿再启动 `models-app`，避免端口冲突）。

---

## 端口与多实例策略

| 变量（Compose 插值） | 默认值    | 作用                                 |
|----------------------|--------|------------------------------------|
| `APP_PORT` | `8083` | **models-app** 宿主机 → 容器 `8083`     |
| `APP_PORT_GPU` | `8081` | **models-app-gpu** 宿主机 → 容器 `8083` |

| 策略 | 做法                                                                      |
|------|-------------------------------------------------------------------------|
| **默认 API + 可选 GPU API 并存** | 不停止 `models-app`；GPU 使用 **`8081`** 访问 `models-app-gpu`。                 |
| **只要一个 GPU 全功能实例** | `stop models-app`，**`APP_PORT_GPU=8083`**，只起 `small-model-gpu` profile。 |
| **只要轻量 API、不要 GPU 栈** | 不使用 `--profile small-model-gpu`，或不启动 `models-app-gpu`。                  |

---

## 卷与持久化策略

### 业务源码与配置（bind mount · 离线迭代）

| 宿主机路径（默认） | 容器路径 | 用途 |
|--------------------|----------|------|
| `${APP_SOURCE_HOST_PATH:-/opt/deploy/models_app_framework/app}` | `/workspace/app` | Python 业务代码（NL2SQL、RAG、Chatbot 等） |
| `${CONFIGS_HOST_PATH:-/opt/deploy/models_app_framework/configs}` | `/workspace/configs` | `prompts.yaml`、`nl2sql_business/**` 等 |

- **镜像构建（有网机）**：`pip install`、`COPY app/configs` 写入镜像，作为 mount 未就绪时的兜底。
- **运行时（离线机）**：以 **bind mount 为准**；更新代码/配置后 **`docker compose restart models-app`**，**无需 rebuild**。
- **仍须 rebuild 镜像**：`requirements-*.txt` 变更、Dockerfile/底座升级、新增系统库等需联网安装的内容。
- 首次部署：在宿主机准备 `/opt/deploy/models_app_framework/{app,configs}` 并同步代码/配置（rsync/scp/tar），再 `docker compose up -d`。

### 数据、模型与其它挂载

| 卷名 / 挂载源 | 挂载到 | 用途 |
|---------------|--------|------|
| `${REDIS_DATA_HOST_PATH}`（默认 `/aidata/data/redis_data`） | Redis 容器 `/data` | 会话/AOF 持久化（Redis 自身） |
| `${MINIO_DATA_HOST_PATH}`（默认 `/aidata/data/minio_data`） | MinIO 容器 `/data` | MinIO 对象数据 |
| `./logs` 或 `../logs`（与 compose 文件位置对应，均为 **app-deploy/logs**） | 应用 `/workspace/logs` | 应用文件日志（`LOG_FILE`） |
| `${SESSION_STORAGE_HOST_PATH}` 等 | 应用 `/workspace/data/...` | 会话对象存储备份、检修任务、FAISS；见 `.env.example` |
| `huggingface-cache` | 应用容器 `/root/.cache/huggingface` | 嵌入/下载模型缓存，减少重复拉取 |
| `small-model-data` | **仅 models-app-gpu** `/workspace/data/small_model_evidence` | 小模型证据片段等可写数据 |
| `SMALL_MODEL_WEIGHTS_HOST_PATH` → `/workspace/models/small:ro` | **仅 models-app-gpu** | 只读权重；未设置时用占位卷 **`small-model-weights-dummy`**（空卷，仅开发联调 compose） |
| `${EMBEDDING_MODELS_HOST_PATH}/Qwen3-Embedding-0.6B` → `/workspace/models/embeddings/Qwen3-Embedding-0.6B:ro` | `models-app` / `models-app-gpu` | **离线嵌入**；配合 `EMBEDDING_MODEL_PATH`、`EMBEDDING_QUERY_PROMPT_NAME=query` |
| `${RERANKER_MODELS_HOST_PATH}/Qwen3-Reranker-0.6B` → `/workspace/models/rerank/...` 或 `/models/rerank/...:ro` | `models-app` / `models-app-gpu` | **离线重排**；`models-app` 用 `/workspace/models/rerank/...`，`models-app-gpu` 用 `/models/rerank/...` |
| `${INTENT_MODELS_HOST_PATH}/chatbot-intent-bert` → `.../chatbot-intent-bert:ro` | `models-app` / `models-app-gpu` | **BERT 意图**（`backend=bert`）；须微调 HF 目录 |
| `${INTENT_LLM_MODELS_HOST_PATH}/qwen2.5-0.5b-instruct` → `.../qwen2.5-0.5b-instruct:ro` | `models-app` / `models-app-gpu` | **轻量意图 LLM**（`backend=llm`）；HF 目录直挂，见 `docs/智能客服意图识别轻量LLM接入说明.md` |

> 多卡环境建议在 `.env` 显式设置 **`EMBEDDING_DEVICE`**（如 `cuda:0`）与 **`RAG_RERANKER_DEVICE`**（如 `cuda:1`），与 vLLM 分卡。仅 **GPU 栈**（`docker-nvidia` / `docker-mx` / `small-model-gpu`）下生效；默认 CPU 栈无效。

---

## 验证与健康检查

建议先按 `deploy-docs/online-services-oncall-runbook.md` 执行 5 分钟检查，再按本节做部署态验证。

```bash
# 应用存活
curl -s "http://127.0.0.1:${APP_PORT:-8083}/health/"

# 应用指标
curl -s "http://127.0.0.1:${APP_PORT:-8083}/metrics" | head

# vLLM
curl -s "http://127.0.0.1:8000/health"

# EasySearch（自签名证书场景）
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"
```

最小通过标准：

- `/health/` 返回 `status: ok`
- `/metrics` 可返回 Prometheus 文本
- vLLM 与 EasySearch 健康检查可通过

---

## 日常运维命令

以下均在 **`app/app-deploy`** 下执行。

```bash
# 默认栈日志
docker compose logs -f models-app

# GPU 实例日志
docker compose logs -f models-app-gpu

# 重启应用（默认）
docker compose restart models-app

# 停止并删除本 compose 内的容器与默认网络（不删外部 vLLM/EasySearch）
docker compose down
```

带 profile 的停止（若曾启动 GPU 服务）：

```bash
docker compose --profile small-model-gpu down
```

---

## 在线业务与离线训练（概念说明）

**在线（随本部署对外提供）**：`app/main.py` 注册的路由，如 `/llm/*`、`/chatbot/*`、`/analysis/*`、`/nl2sql/*`、`/rag/*`、`/health/*`、`/metrics`、`/small-model/*`（GPU 实例上更完整）。

**离线或需谨慎暴露**：`/dajia/*` 等大模型训练管理、小模型训练脚本等——**不一定**每台在线节点都需要训练依赖；生产可对网关屏蔽路径或拆镜像。

---

## 网络与 DNS

- **models-app** 与 **models-app-gpu** 均加入：**`app-internal`** + 外部 **`vllm-external`**（默认 `docker_vllm-network`）+ **`rag-external`**（默认 `ai-stack`）+ **`mineru-external`**（默认 `mineru-stack`）。  
- 因此容器内可使用：**`http://vllm-service:8000`**、**`https://rag-easysearch:9200`**、**`http://mineru-api:8000`**、**`redis://redis:6379`**。  
- 若自定义容器名或网络名，同步修改 `.env` 中的 **`LLM_DEFAULT_ENDPOINT`**、**`RAG_ES_HOSTS`**、**`MINERU_BASE_URL`**、**`VLLM_DOCKER_NETWORK`**、**`RAG_DOCKER_NETWORK`**、**`MINERU_DOCKER_NETWORK`**。

---

## 小模型权重与 YAML 路径约定

本 compose 将 **`SMALL_MODEL_WEIGHTS_HOST_PATH`** 挂载为容器 **`/workspace/models/small`（只读）**。

- **`configs/small_models.yaml`** 中示例路径形如 **`models/small/helmet/best.pt`**：相对 **`WORKDIR /workspace`**，即容器内 **`/workspace/models/small/helmet/best.pt`**。  
  因此宿主机目录中应有 **`helmet/best.pt`**（若 YAML 如此写）。  
- **`configs/small_model_algorithms.yaml`** 中 `weights_path` 若写 **`app/small_models/pretrained/call.pt`**，则依赖镜像内 `app/` 下文件，与上述宿主机挂载无关；统一放宿主机权重时，建议改为 **`models/small/...`** 等与挂载一致的相对路径。

---

## 嵌入与 Hugging Face 缓存（含离线模型目录约定）

当前默认嵌入/重排为 **Qwen3-Embedding-0.6B**（1024 维）与 **Qwen3-Reranker-0.6B**。命名卷 **`huggingface-cache`** 挂到 **`/root/.cache/huggingface`**。

### Qwen3 与 BGE 的差异（运维必读）

| 项 | Qwen3（当前默认） | 原 BGE（历史） |
|----|-------------------|----------------|
| 向量维度 | **1024** | bge-small：**512** |
| 嵌入编码 | query 用 `prompt_name=query`，document 不带 prompt | 统一 encode |
| 环境变量 | `EMBEDDING_QUERY_PROMPT_NAME`、`EMBEDDING_TRUST_REMOTE_CODE`、`EMBEDDING_DEVICE` | 一般仅需 `EMBEDDING_MODEL_PATH` |
| 重排 | `RAG_RERANKER_TRUST_REMOTE_CODE=true`；分数为 logit | 通常 0~1 附近 |
| 依赖 | 建议 `sentence-transformers>=5.4`、`transformers>=4.51`（见 `requirements-大模型应用.txt`） | ST 2.x 即可 |

### 在线/默认策略

- 未显式指定 `EMBEDDING_MODEL_PATH` 时，应用会尝试从 Hugging Face Hub 在线下载，并缓存到上述目录。  
- **CPU 栈**（`docker-compose.yml`）下嵌入/重排实际跑在 **CPU**；配置 `EMBEDDING_DEVICE=cuda:0` 不会生效。

### 离线优先策略（推荐生产做法）

1. **宿主机离线模型目录约定**

   ```text
   /aidata/models/
     embeddings/
       Qwen3-Embedding-0.6B/    # Qwen/Qwen3-Embedding-0.6B 完整 HF 目录
     reranker/
       Qwen3-Reranker-0.6B/     # Qwen/Qwen3-Reranker-0.6B 完整 HF 目录
     intent/
       chatbot-intent-bert/     # 可选，BERT 意图
     llm/
       qwen2.5-0.5b-instruct/   # 可选，轻量意图 LLM
   ```

   离线下载示例（魔塔 / HuggingFace，目录名与 compose 挂载子路径一致）：

   ```bash
   mkdir -p /aidata/models/embeddings /aidata/models/reranker
   # huggingface-cli download Qwen/Qwen3-Embedding-0.6B --local-dir /aidata/models/embeddings/Qwen3-Embedding-0.6B
   # huggingface-cli download Qwen/Qwen3-Reranker-0.6B --local-dir /aidata/models/reranker/Qwen3-Reranker-0.6B
   ```

   > **轻量意图 LLM**：`huggingface-cli download Qwen/Qwen2.5-0.5B-Instruct --local-dir /aidata/models/llm/qwen2.5-0.5b-instruct`；见 `docs/智能客服意图识别轻量LLM接入说明.md`。

   > **注意**：`chatbot-intent-bert` **不能**直接放魔塔下载的 `bert-base-chinese` 等通用预训练模型。  
   > 当前代码使用 `AutoModelForSequenceClassification`，**必须**是已完成三分类微调并导出的模型。  
   > 不想训练请保持 `CHATBOT_INTENT_BACKEND=rules`（默认）。

   并在 `app/app-deploy/.env` 中配置：

   - `EMBEDDING_MODELS_HOST_PATH=/aidata/models/embeddings`
   - `RERANKER_MODELS_HOST_PATH=/aidata/models/reranker`
   - `INTENT_MODELS_HOST_PATH=/aidata/models/intent`（启用 BERT 意图时）
   - `INTENT_LLM_MODELS_HOST_PATH=/aidata/models/llm`（启用轻量意图 LLM 时）

2. **在 compose 中挂载到应用容器**

   当前 **`docker-compose.yml` / `docker-nvidia/` / `docker-mx/`** 已预置 Qwen3 挂载，一般只需保证宿主机目录存在。若更换模型子目录名，需同步改三处 compose 中 volume 与 `.env` 路径。

   ```yaml
   # models-app 示例（路径以实际 compose 为准）
   volumes:
     - ${EMBEDDING_MODELS_HOST_PATH:-/aidata/models/embeddings}/Qwen3-Embedding-0.6B:/workspace/models/embeddings/Qwen3-Embedding-0.6B:ro
     - ${RERANKER_MODELS_HOST_PATH:-/aidata/models/reranker}/Qwen3-Reranker-0.6B:/workspace/models/rerank/Qwen3-Reranker-0.6B:ro
   environment:
     - RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
   ```

3. **在 `.env` 中指定嵌入/重排运行参数**

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

   # 换嵌入模型后须升版本并 re-ingest
   RAG_ES_INDEX_VERSION=3
   RAG_ES_AUTO_MIGRATE_ON_START=true
   ```

   启动后日志应出现：`EmbeddingService: loaded ... embedding_dim=1024 device=...` 与 `RAGService loaded CrossEncoder reranker: ... device=...`。

4. **启动/重启应用栈**

   见上文 [部署形态选择](#部署形态选择cpu--英伟达-gpu--沐曦-gpu) 中对应 `docker compose` 命令。

更换嵌入、重排或 BERT 意图模型时：

- 在宿主机 `${EMBEDDING_MODELS_HOST_PATH}` / `${RERANKER_MODELS_HOST_PATH}` 下放置新模型子目录；  
- 同步 compose 挂载子目录名与 `.env` 中 `EMBEDDING_MODEL_PATH` / `RAG_RERANKER_MODEL_PATH`；  
- **维度变化时**递增 `RAG_ES_INDEX_VERSION` 并 **全量 re-ingest**。

---

## 故障排查

| 现象 | 检查项 |
|------|--------|
| 应用连不上 vLLM / EasySearch | `docker network ls` 与 `.env` 中 `*_DOCKER_NETWORK` 是否一致；对端容器是否在同一网络。 |
| 应用连不上 MinerU | `MINERU_ENABLED` 是否开启；`MINERU_BASE_URL` 是否为 `http://mineru-api:8000`；`MINERU_DOCKER_NETWORK` 是否存在并与 mineru-deploy 一致。 |
| 检修 V0 报 **`layout_ocr` 503** / **`DOCX_TO_PDF_UNAVAILABLE`** | 侧车未起或镜像内无 LibreOffice：见 **`paddleocr-layout-deploy/README.md`**；主应用可设 **`INSPECT_EXTRACT_V0_DOCX_USE_LAYOUT_OCR=false`** 临时绕过 docx 侧车；**`INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT`** 须为 **`http://paddleocr-layout-api:8000`**（同网）。 |
| **`Name or service not known`（paddleocr-layout-api）** | **`PADDLE_LAYOUT_DOCKER_NETWORK`** 与侧车 **`PADDLE_LAYOUT_NETWORK_NAME`** 不一致，或 **`models-app` 未加入 external 网络**。 |
| `Connection refused` 使用 `127.0.0.1` 访问对端服务 | 在容器内 `127.0.0.1` 是自身，应改用 **`vllm-service` / `rag-easysearch`**。 |
| EasySearch TLS / 401 | HTTPS + `RAG_ES_VERIFY_CERTS`；用户名密码与库一致。 |
| GPU 容器 `cuda: False` | 是否使用 **GPU 栈**（`docker-nvidia` / `docker-mx`）；宿主机 Toolkit；`nvidia-smi` 是否正常。 |
| 配置了 `EMBEDDING_DEVICE=cuda:0` 但仍为 CPU | 是否误用 **默认 CPU 栈** `docker-compose.yml`；应改用 `docker-nvidia` 或 `docker-mx`。 |
| 嵌入加载失败 / `trust_remote_code` | Qwen3 设 `EMBEDDING_TRUST_REMOTE_CODE=true`；检查离线目录是否完整。 |
| 重排 skip rerank / 加载失败 | `RAG_RERANKER_TRUST_REMOTE_CODE=true`；离线路径与 compose `RAG_RERANKER_MODEL_PATH` 一致；GPU 栈是否可用。 |
| RAG 语义检索无结果 / 维度错误 | 换嵌入后是否 **升 `RAG_ES_INDEX_VERSION` 并 re-ingest**；`embedding_dim` 是否为 **1024**。 |
| RAG 重排耗时过高（rerank 慢） | 日志 `RAGService rerank done ... device=...`；GPU 栈 + `RAG_RERANKER_DEVICE=cuda:1` 与 vLLM 分卡。 |
| FAQ 软直通从不触发 | Qwen3 rerank 为 logit 分，重调 `CHATBOT_FAQ_SOFT_DIRECT_MIN_SCORE` 或暂时关闭。 |
| YOLO 找不到权重 | **`SMALL_MODEL_WEIGHTS_HOST_PATH`** 是否设置；宿主机路径与 YAML 是否对齐。 |
| `docker compose` 报外部网络不存在 | 先启动 `rag_db-deploy`、`vllm-deploy`（`deploy.sh`）、（可选）`mineru-deploy`，或先执行 `docker network create <network-name>`。 |

更全的应用侧环境变量以 **`app/core/config.py`** 的 **`_load_from_env()`** 为准；**.env.example** 按块注释说明了常用项。

---

## 运维检查清单（表格）

以下为上线/变更前建议逐项核对；**「结果」列**可自行打勾（复制到本地文档或打印后手写）。

### A. 宿主机与 Docker

| 序号 | 检查项 | 说明 / 通过标准 | 结果 |
|:----:|--------|------------------|:----:|
| A1 | Docker Engine 与 Compose V2 可用 | `docker version`、`docker compose version` 无报错 | ☐ |
| A2 | 磁盘与内存 | 镜像构建、HF 缓存、日志有足够空间；内存满足并发预期 | ☐ |
| A3 | Linux：`vm.max_map_count`（若使用 EasySearch） | 已按 `rag_db-deploy` 文档调整，EasySearch 能绿 | ☐ |
| A4 | 外部 Docker 网络已存在 | `docker network ls` 中可见 `RAG_DOCKER_NETWORK`、`VLLM_DOCKER_NETWORK` 对应项 | ☐ |

### B. 外部依赖（须先于 `app/app-deploy` 启动）

| 序号 | 检查项 | 说明 / 通过标准 | 结果 |
|:----:|--------|------------------|:----:|
| B1 | EasySearch 已运行 | `docker ps` 见 `rag-easysearch`（或实际容器名）；健康检查通过 | ☐ |
| B2 | vLLM 已运行 | `docker ps` 见 `vllm-service`；宿主机或同网络可访问推理端口 | ☐ |
| B3 | MinerU（可选）已运行 | `docker ps` 见 `mineru-api`；`/health` 可访问；网络 `mineru-stack` 可见 | ☐ |
| B3b | Paddle 版面侧车（可选，检修 V0） | `docker ps` 见 `paddleocr-layout-api`；宿主机 `8010/health` 可访问；网络 **`paddle-layout-stack`** 与 **`PADDLE_LAYOUT_DOCKER_NETWORK`** 一致 | ☐ |
| B4 | 业务 MySQL（若用 NL2SQL 等） | 宿主机或网络内可连；账号与 `DB_URL` / `DB_*` 一致 | ☐ |

### C. `app/app-deploy` 配置（`.env`）

| 序号 | 检查项 | 说明 / 通过标准 | 结果 |
|:----:|--------|------------------|:----:|
| C1 | 已从 `.env.example` 复制为 `.env` | 同目录；未将含真实密钥的 `.env` 提交 Git | ☐ |
| C2 | `LLM_DEFAULT_ENDPOINT` | 容器内可解析（如 `http://vllm-service:8000/v1`），非 `127.0.0.1` 指错目标 | ☐ |
| C3 | `LLM_DEFAULT_MODEL` | 与 vLLM `--served-model-name` 一致 | ☐ |
| C4 | `RAG_ES_HOSTS` / 账号 / 证书 | 与 EasySearch 协议一致（常为 `https://rag-easysearch:9200`）；`RAG_ES_VERIFY_CERTS` 与证书策略一致 | ☐ |
| C5 | `RAG_ES_PASSWORD` 等 | 与 EasySearch `admin` 实际密码一致 | ☐ |
| C6 | `REDIS_URL` | 指向本 compose 内 `redis`（如 `redis://redis:6379/0`） | ☐ |
| C7 | `DB_URL` 或 `DB_*` | 密码特殊字符已 URL 编码或按 `config.py` 规则配置；库可达 | ☐ |
| C8 | `MINERU_*`（可选） | 启用 MinerU 时，`MINERU_BASE_URL`、`MINERU_IO_CONTAINER_PATH`、`MINERU_MAX_CONCURRENT` 已按部署设定 | ☐ |
| C8b | **`INSPECT_EXTRACT_V0_*`**（可选） | 启用检修 V0 时，`INSPECT_EXTRACT_V0_ENABLED=true`，**`INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT=http://paddleocr-layout-api:8000`**，且 **`PADDLE_LAYOUT_DOCKER_NETWORK`** 已配置 | ☐ |
| C9 | `VLLM_DOCKER_NETWORK` / `RAG_DOCKER_NETWORK` / `MINERU_DOCKER_NETWORK` / **`PADDLE_LAYOUT_DOCKER_NETWORK`** | 与 `docker network ls` 中外部网络名一致（启用侧车时 **`paddle-layout-stack`** 须存在） | ☐ |
| C10 | `APP_PORT`（及 `APP_PORT_GPU` 若启用） | 与宿主机防火墙、上游反代、无端口冲突 | ☐ |

### D. 可选：小模型 GPU profile（`--profile small-model-gpu`）

| 序号 | 检查项 | 说明 / 通过标准 | 结果 |
|:----:|--------|------------------|:----:|
| D1 | 宿主机 NVIDIA 驱动 | `nvidia-smi` 正常 | ☐ |
| D2 | Docker GPU | Linux：NVIDIA Container Toolkit；Win：Docker Desktop GPU 已启用 | ☐ |
| D3 | `SMALL_MODEL_WEIGHTS_HOST_PATH` | 生产为宿主机绝对路径；与 `configs` 中权重相对路径能拼成容器内有效文件 | ☐ |
| D4 | `configs/small_model_algorithms.yaml` 等 | `device` 为 `cuda:0` / `0`（GPU）；仅 CPU 则不必起 GPU profile | ☐ |
| D5 | 端口策略 | 与 `models-app` 同时存在时 `APP_PORT_GPU` 避免与 `APP_PORT` 冲突；单实例策略已按文档执行 | ☐ |

### E. 部署与发布后验证

| 序号 | 检查项 | 说明 / 通过标准 | 结果 |
|:----:|--------|------------------|:----:|
| E1 | 启动顺序已遵守 | `rag_db-deploy` → `vllm-deploy` → （可选）`mineru-deploy` → （可选）**`paddleocr-layout-deploy`** → `app/app-deploy` | ☐ |
| E2 | 容器均为 Up | `docker compose ps`（本目录）无反复重启 | ☐ |
| E3 | 健康检查 | `curl` 访问 `/health/` 返回 `ok`（端口按 `APP_PORT` / `APP_PORT_GPU`） | ☐ |
| E4 | 指标 | `GET /metrics` 可访问（Prometheus 文本） | ☐ |
| E5 | 可选：GPU 容器内 CUDA | `docker exec models-app-gpu python -c "import torch; print(torch.cuda.is_available())"` 为 `True` | ☐ |

### F. 安全与运维（建议）

| 序号 | 检查项 | 说明 / 通过标准 | 结果 |
|:----:|--------|------------------|:----:|
| F1 | 密钥与配置 | 生产密钥来自 Secret/保险库；`.env` 权限收紧 | ☐ |
| F2 | 网关与 TLS | 对外 HTTPS、限流、按需屏蔽 `/dajia/*` 等内部训练接口 | ☐ |
| F3 | 备份 | Redis/EasySearch/业务库按 RPO/RTO 有备份或快照策略 | ☐ |
| F4 | 日志与监控 | 容器日志采集；完整监控见仓库根目录 `monitoring-deploy/`（Prometheus 抓取 `/metrics` + Grafana）；方案：`docs/系统Prometheus资源监控实现方案.md` | ☐ |

---

## 与框架文档的对应

| 文档 | 用途 |
|------|------|
| `memory-bank/00-project-overview.md` | 项目边界与能力总览 |
| `memory-bank/01-architecture.md` | 分层架构与在线/训练角色 |
| `framework-guide/数据持久化与容器部署说明.md` | Redis / ES / DB / 挂载约定 |
| `framework-guide/框架架构与调用链路总览.md` | API 前缀与调用链 |
| `framework-guide/报告解析企业级实现方案.md` | 现网检修解析（`/inspection-extract`）主方案 |
| `framework-guide/报告解析企业级实现方案V0.md` | 检修 V0 设计级方案 |
| `enterprise-level_transformation_docs/企业级检修报告结构化提取实现和使用说明.md` | 现网检修企业级使用说明 |
| **`enterprise-level_transformation_docs/企业级检修报告结构化提取V0版本实现和使用说明.md`** | **V0 + `paddleocr-layout-deploy` 部署与排障** |
| **`paddleocr-layout-deploy/README.md`** | **版面侧车** CPU / 英伟达 / 沐曦编排与 LibreOffice |
| `rag_db-deploy/README.md` | EasySearch |
| `vllm-deploy/README.md` | vLLM |

---

## 本目录文件清单

| 文件 | 说明 |
|------|------|
| `Dockerfile` | 默认 **CPU** 镜像：`requirements`（有网 build）+ `app`/`configs`（COPY fallback，运行时 mount 覆盖） |
| `Dockerfile.small-model-gpu` | GPU 小模型镜像：cu121 PyTorch + 大小模型 requirements + `ultralytics` |
| `docker-compose.yml` | **CPU** 栈：`redis`、`minio`、`models-app`；可选 **`models-app-gpu`**（`profiles: small-model-gpu`） |
| **`docker-nvidia/Dockerfile-nvidia`** | **英伟达 GPU** 主镜像：cu121 PyTorch + 大模型应用依赖（嵌入/重排 GPU） |
| **`docker-nvidia/docker-compose-nvidia.yml`** | **英伟达 GPU** 栈；可选 `small-model-gpu` profile |
| **`docker-nvidia/README.md`** | 英伟达 GPU 栈快速说明 |
| **`docker-mx/Dockerfile-mx`** | **沐曦 GPU** 主镜像：Metax 基础镜像 + conda |
| **`docker-mx/docker-compose-mx.yml`** | **沐曦 GPU** 栈 |
| **`docker-ascend/Dockerfile-ascend`** | **昇腾 Ascend** 主镜像：`vllm-ascend:v0.23.0-310p` + 业务依赖 |
| **`docker-ascend/docker-compose-ascend.yml`** | **昇腾 Ascend** 栈 |
| **`docker-ascend/README.md`** | 昇腾栈快速说明 |
| `.env.example` | 环境变量模板（含 **APP_SOURCE_HOST_PATH**、英伟达/沐曦/昇腾注释、Qwen3 嵌入/重排） |
| `README.md` | 本文档 |
| `README-simple-deploy.md` | 简明上线流程 |
| `README-external-services-lan-deploy.md` | 局域网/离线外挂服务 |
