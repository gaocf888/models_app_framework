# 外挂服务部署说明（局域网/离线场景）
该文档集成了 所有外挂服务的部署策略，部署时直接参照该文档(或者根据对应外挂服务部署目录中的文档进行部署)进行外挂服务的部署即可

本文用于 `app/app-deploy` 的外挂服务部署指引，覆盖：
- `vllm-deploy`
- `rag_db-deploy`
- `mineru-deploy`
- **`paddleocr-layout-deploy`**（检修结构化 V0 · PaddleOCR 版面侧车，可选）

目标：结构简单、步骤明确，支持“有网服务器构建 -> 局域网服务器导入启动”。
在线业务值班排障请参考：`deploy-docs/online-services-oncall-runbook.md`（当前先覆盖智能客服）。

---

## 1. 结论先看

- 初次部署最耗时，通常是**镜像构建与依赖下载**（尤其 `vllm-deploy`、`mineru-deploy`）。
- 局域网/离线部署建议：在有网机器构建并验证后，导出镜像到离线机器导入。
- 仅导入镜像不够，还必须同步：
  - `.env` 配置
  - 模型目录（宿主机挂载）
  - 数据目录/卷（特别是 EasySearch）
  - 外部网络（如 `mineru-stack`、**`paddle-layout-stack`**）

---

## 2. 三类外挂服务清单

| 服务 | 目录 | 离线部署关键点 |
|------|------|----------------|
| vLLM | `vllm-deploy/` | 导入镜像 + 准备模型目录（`MODEL_PATH`） |
| EasySearch | `rag_db-deploy/` | 导入镜像 + 数据卷/索引迁移（如需保留历史数据） |
| MinerU | `mineru-deploy/` | 导入镜像 + 模型目录（`MINERU_MODELS_HOST_PATH`）+ 共享 IO 目录（`MINERU_IO_HOST_PATH`） |
| **Paddle 版面侧车** | **`paddleocr-layout-deploy/`** | 导入镜像 + 模型目录（`PADDLE_LAYOUT_MODELS_HOST_PATH`）+ IO（`PADDLE_LAYOUT_IO_HOST_PATH`）；与主应用 **`PADDLE_LAYOUT_DOCKER_NETWORK`** 对齐 |

---

## 3. 有网服务器：构建与导出

以下命令建议在项目根目录执行（按需调整镜像名）。

### 3.1 vLLM

```bash
cd vllm-deploy
cp .env.example .env
cd docker
docker compose --env-file ../.env build
docker save -o vllm-service-latest.tar vllm-service:latest
```

### 3.2 MinerU（CPU 示例）

```bash
cd ../../mineru-deploy
cp .env.example .env
docker compose --env-file .env -f docker-compose.cpu.yml build
docker save -o mineru-cpu.tar mineru-cpu:py311
```

> 若使用自定义镜像名，请以 `.env` 中 `MINERU_CPU_IMAGE` 为准。

### 3.3 EasySearch（示例）

```bash
cd ../rag_db-deploy
cp .env.example .env
docker compose -f docker-compose.easysearch_bak0.yml --env-file .env pull
docker save -o easysearch.tar infiniflow/easysearch:latest
```

> EasySearch 实际镜像名请以 `rag_db-deploy` compose 文件为准。

### 3.4 Paddle 版面侧车（检修 V0，CPU 示例）

```bash
cd ../../paddleocr-layout-deploy
cp .env.example .env
docker compose -f docker-compose.cpu.yml build
docker save -o paddleocr-layout-cpu.tar paddleocr-layout-api:py311-cpu
```

> 镜像名以 `.env` 中 **`PADDLE_LAYOUT_CPU_IMAGE`** 为准；GPU 版见该目录 **`docker-compose.gpu.nvidia.yml`** / **`docker-compose.gpu.mthreads.yml`**。

---

## 4. 局域网服务器：导入与准备

### 4.1 导入镜像

```bash
docker load -i vllm-service-latest.tar
docker load -i mineru-cpu.tar
docker load -i easysearch.tar
docker load -i paddleocr-layout-cpu.tar
```

### 4.2 准备目录（必须）

```bash
# vLLM 模型目录（示例）
mkdir -p /aidata/models/llm

# app RAG 离线模型目录（Qwen3 嵌入/重排，示例）
mkdir -p /aidata/models/embeddings/Qwen3-Embedding-0.6B
mkdir -p /aidata/models/reranker/Qwen3-Reranker-0.6B

# MinerU 模型与 IO 目录（示例，与 mineru-deploy/.env.example 中 MINERU_*_HOST_PATH 一致）
mkdir -p /aidata/mineru/models
mkdir -p /aidata/mineru/io

# Paddle 版面侧车模型与 IO（示例，与 paddleocr-layout-deploy/.env.example 一致）
mkdir -p /aidata/paddle_layout/models
mkdir -p /aidata/paddle_layout/io

```

### 4.3 准备 external 网络（建议先建）

```bash
docker network create mineru-stack || true
docker network create paddle-layout-stack || true
```

---

## 5. 各服务离线启动顺序（推荐）

1. `rag_db-deploy`（EasySearch）
2. `vllm-deploy`
3. `mineru-deploy`（若启用）
4. **`paddleocr-layout-deploy`**（若启用检修 V0 版面侧车）
5. `app/app-deploy`

---

## 6. 关键配置对齐（最容易错）

### 6.1 app 与 MinerU

`app/app-deploy/.env`：

```env
MINERU_ENABLED=true
MINERU_BASE_URL=http://mineru-api:8000
MINERU_DOCKER_NETWORK=mineru-stack
MINERU_IO_CONTAINER_PATH=/workspace/mineru-io
MINERU_IO_HOST_PATH=/aidata/mineru/io
```

`mineru-deploy/.env`：

```env
MINERU_NETWORK_NAME=mineru-stack
MINERU_MODELS_HOST_PATH=/aidata/mineru/models
MINERU_IO_HOST_PATH=/aidata/mineru/io
# 离线必须 local：权重读 /models ← MINERU_MODELS_HOST_PATH
# （默认 modelscope 缓存在容器 /root/.cache/modelscope，不会持久化到 IO；
#  huggingface 在线才写 ${MINERU_IO_HOST_PATH}/.hf_cache。见 mineru-deploy/README.md §4）
MINERU_MODEL_SOURCE=local
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

### 6.2 app 与 vLLM / RAG 嵌入重排（Qwen3）

`app/app-deploy/.env`：

```env
LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1
LLM_DEFAULT_MODEL=<与 vllm served_model_name 一致>
VLLM_DOCKER_NETWORK=<与 vllm 实际网络名一致>

# 宿主机模型根目录（compose 拼接 Qwen3 子目录挂载）
EMBEDDING_MODELS_HOST_PATH=/aidata/models/embeddings
RERANKER_MODELS_HOST_PATH=/aidata/models/reranker

# Qwen3 嵌入/重排（容器内路径，与 compose 挂载一致）
EMBEDDING_MODEL_PATH=/workspace/models/embeddings/Qwen3-Embedding-0.6B
EMBEDDING_QUERY_PROMPT_NAME=query
EMBEDDING_TRUST_REMOTE_CODE=true
RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
RAG_RERANKER_TRUST_REMOTE_CODE=true

# GPU 栈（docker-nvidia / docker-mx）下与 vLLM 分卡；CPU 栈（docker-compose.yml）无效
# EMBEDDING_DEVICE=cuda:0
# RAG_RERANKER_DEVICE=cuda:1
# MODELS_APP_NVIDIA_VISIBLE_DEVICES=all   # 仅 docker-nvidia 栈

# 从 BGE 升级或换嵌入后：升版本 + 全量 re-ingest
# RAG_ES_INDEX_VERSION=3
# RAG_ES_AUTO_MIGRATE_ON_START=true
```

**应用 compose 选型**（与 `README.md` 一致）：

| 场景 | 启动命令 |
|------|----------|
| CPU | `cd app/app-deploy && docker compose up -d --build` |
| 英伟达 GPU | `docker compose -f docker-nvidia/docker-compose-nvidia.yml up -d --build` |
| 沐曦 GPU | `cd docker-mx && docker compose -f docker-compose-mx.yml up -d --build` |

离线迁移时须同步拷贝 `${EMBEDDING_MODELS_HOST_PATH}/Qwen3-Embedding-0.6B` 与 `${RERANKER_MODELS_HOST_PATH}/Qwen3-Reranker-0.6B` 完整 HF 目录。

`vllm-deploy/.env`：

```env
MODEL_PATH=/aidata/models/llm
```

### 6.3 app 与 Paddle 版面侧车（检修 V0，可选）

`app/app-deploy/.env`（与 **`paddleocr-layout-deploy/.env`** 中 **`PADDLE_LAYOUT_NETWORK_NAME`** 一致）：

```env
INSPECT_EXTRACT_V0_ENABLED=true
INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT=http://paddleocr-layout-api:8000
PADDLE_LAYOUT_DOCKER_NETWORK=paddle-layout-stack
```

`paddleocr-layout-deploy/.env`：

```env
PADDLE_LAYOUT_NETWORK_NAME=paddle-layout-stack
PADDLE_LAYOUT_MODELS_HOST_PATH=/aidata/paddle_layout/models
PADDLE_LAYOUT_IO_HOST_PATH=/aidata/paddle_layout/io
```

侧车详细变量见 **`paddleocr-layout-deploy/.env.example`** 与 **`enterprise-level_transformation_docs/企业级检修报告结构化提取V0版本实现和使用说明.md`**。

---

## 7. 数据与模型迁移说明

- `vllm-deploy`：通常迁移模型目录即可；镜像只包含服务与依赖。
- **`app/app-deploy`**：须迁移 **Qwen3 嵌入/重排** 宿主机目录（`${EMBEDDING_MODELS_HOST_PATH}/Qwen3-Embedding-0.6B`、`${RERANKER_MODELS_HOST_PATH}/Qwen3-Reranker-0.6B`）；换嵌入维度时递增 `RAG_ES_INDEX_VERSION` 并 re-ingest。
- `mineru-deploy`：模型目录和 IO 目录都要迁移/保留。
- **`paddleocr-layout-deploy`**：模型目录（`PADDLE_LAYOUT_MODELS_HOST_PATH`）与 IO（`PADDLE_LAYOUT_IO_HOST_PATH`）建议一并迁移；详见该目录 README。
- `rag_db-deploy`：
  - 若只要空库，导入镜像后直接启动即可；
  - 若要保留历史索引，需迁移数据卷或做快照恢复（按 `rag_db-deploy` 文档）。

---

## 8. 最小验证清单

启动后依次验证：

```bash
# vLLM
curl -s http://127.0.0.1:8000/health

# MinerU
curl -s http://127.0.0.1:8009/health

# Paddle 版面侧车（端口以 compose 映射为准，默认 8010）
curl -sS http://127.0.0.1:8010/health

# app
curl -s http://127.0.0.1:8083/health/
```

若 `app` 启动时报 external 网络不存在，先执行：

```bash
docker network create mineru-stack
docker network create paddle-layout-stack
```

---

## 9. 常见问题

- `Network mineru-stack declared as external, but could not be found`
  - 原因：external 网络未创建。
  - 处理：`docker network create mineru-stack`。

- `pip` 下载超时 / hash mismatch
  - 原因：公网链路不稳、大包中断。
  - 处理：优先在有网机器构建，离线机仅 `docker load`。

- app 连不上 MinerU
  - 检查 `MINERU_BASE_URL` 是否为 `http://mineru-api:8000`（不是宿主机端口）。
  - 检查 `MINERU_DOCKER_NETWORK` 与 `MINERU_NETWORK_NAME` 是否同名。

- **`Network paddle-layout-stack declared as external, but could not be found`**
  - 处理：`docker network create paddle-layout-stack`，或先启动 **`paddleocr-layout-deploy`** compose 以创建该网络。

- app 解析 **`paddleocr-layout-api` 失败**（`Name or service not known`）
  - 检查 **`PADDLE_LAYOUT_DOCKER_NETWORK`** 是否与侧车 **`PADDLE_LAYOUT_NETWORK_NAME`** 一致，且 **`models-app` 已加入该 external 网络**；`INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT` 须为 **`http://paddleocr-layout-api:8000`**（勿在容器内用宿主机 `127.0.0.1:8010`）。
