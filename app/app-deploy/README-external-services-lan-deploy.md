# 外挂服务部署说明（局域网/离线场景）
该文档集成了 所有外挂服务的部署策略，部署时直接参照该文档(或者根据对应外挂服务部署目录中的文档进行部署)进行外挂服务的部署即可

本文用于 `app/app-deploy` 的外挂服务部署指引，覆盖：
- `vllm-deploy`
- `rag_db-deploy`
- `mineru-deploy`

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
  - 外部网络（如 `mineru-stack`）

---

## 2. 三类外挂服务清单

| 服务 | 目录 | 离线部署关键点 |
|------|------|----------------|
| vLLM | `vllm-deploy/` | 导入镜像 + 准备模型目录（`MODEL_PATH`） |
| EasySearch | `rag_db-deploy/` | 导入镜像 + 数据卷/索引迁移（如需保留历史数据） |
| MinerU | `mineru-deploy/` | 导入镜像 + 模型目录（`MINERU_MODELS_HOST_PATH`）+ 共享 IO 目录（`MINERU_IO_HOST_PATH`） |

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
docker compose -f docker-compose.easysearch.yml --env-file .env pull
docker save -o easysearch.tar infiniflow/easysearch:latest
```

> EasySearch 实际镜像名请以 `rag_db-deploy` compose 文件为准。

---

## 4. 局域网服务器：导入与准备

### 4.1 导入镜像

```bash
docker load -i vllm-service-latest.tar
docker load -i mineru-cpu.tar
docker load -i easysearch.tar
```

### 4.2 准备目录（必须）

```bash
# vLLM 模型目录（示例）
mkdir -p /opt/models/llm

# app RAG 离线模型目录（示例）
mkdir -p /opt/models/embeddings/bge-small-zh-v1.5
mkdir -p /opt/models/reranker/bge-reranker-large

# MinerU 模型与 IO 目录（示例）
mkdir -p /data/mineru/models
mkdir -p /data/mineru/io

```

### 4.3 准备 external 网络（建议先建）

```bash
docker network create mineru-stack || true
```

---

## 5. 各服务离线启动顺序（推荐）

1. `rag_db-deploy`（EasySearch）
2. `vllm-deploy`
3. `mineru-deploy`（若启用）
4. `app/app-deploy`

---

## 6. 关键配置对齐（最容易错）

### 6.1 app 与 MinerU

`app/app-deploy/.env`：

```env
MINERU_ENABLED=true
MINERU_BASE_URL=http://mineru-api:8000
MINERU_DOCKER_NETWORK=mineru-stack
MINERU_IO_CONTAINER_PATH=/workspace/mineru-io
MINERU_IO_HOST_PATH=/data/mineru/io
```

`mineru-deploy/.env`：

```env
MINERU_NETWORK_NAME=mineru-stack
MINERU_MODELS_HOST_PATH=/data/mineru/models
MINERU_IO_HOST_PATH=/data/mineru/io
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

### 6.2 app 与 vLLM

`app/app-deploy/.env`：

```env
LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1
LLM_DEFAULT_MODEL=<与 vllm served_model_name 一致>
VLLM_DOCKER_NETWORK=<与 vllm 实际网络名一致>
EMBEDDING_MODELS_HOST_PATH=/opt/models/embeddings
RERANKER_MODELS_HOST_PATH=/opt/models/reranker
```

`vllm-deploy/.env`：

```env
MODEL_PATH=/opt/models/llm
```

---

## 7. 数据与模型迁移说明

- `vllm-deploy`：通常迁移模型目录即可；镜像只包含服务与依赖。
- `mineru-deploy`：模型目录和 IO 目录都要迁移/保留。
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

# app
curl -s http://127.0.0.1:8083/health/
```

若 `app` 启动时报 external 网络不存在，先执行：

```bash
docker network create mineru-stack
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

