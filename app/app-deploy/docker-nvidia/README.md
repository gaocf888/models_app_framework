# 英伟达 GPU 应用栈（`docker-nvidia/`）

本目录提供 **英伟达 GPU** 环境下的 `models-app` 部署，与上级目录的 **CPU 栈**（`docker-compose.yml`）及 **沐曦 GPU 栈**（`docker-mx/`）并列。

## 适用场景

- 宿主机为 **英伟达 GPU**，已安装 **NVIDIA Container Toolkit**
- 希望 **Qwen3 嵌入/重排** 跑在 GPU（`EMBEDDING_DEVICE` / `RAG_RERANKER_DEVICE`）
- 可选 `--profile small-model-gpu` 启动 **models-app-gpu**（YOLO、视频通道等）

> 默认 CPU 栈（`docker compose up`）镜像 **无 CUDA PyTorch**，`.env` 中 `EMBEDDING_DEVICE=cuda:0` **不会生效**。

## 文件说明

| 文件 | 说明 |
|------|------|
| `Dockerfile-nvidia` | `python:3.11-slim` + pip **cu121 torch** + `requirements-大模型应用.txt` |
| `docker-compose-nvidia.yml` | Redis、MinIO、`models-app`（GPU）；可选 `models-app-gpu` profile |

`.env` 使用 **`app/app-deploy/.env`**（与 `.env.example` 同目录），compose 通过 `env_file: ../.env` 引用。

业务源码默认 bind mount：`/opt/deploy/models_app_framework/{app,configs}` → 容器 `/workspace/{app,configs}`。离线迭代见上级 `README.md`「卷与持久化策略」。

## 前置条件

1. 外挂服务已启动：EasySearch、vLLM（见 `README-simple-deploy.md` §3.1）
2. **GPU 注入与 vLLM 相同**：宿主机 NVIDIA Container Toolkit 正常，且 compose 中 `models-app` 已声明 `deploy.resources.reservations.devices`（本目录 `docker-compose-nvidia.yml` 已配置；勿仅依赖 `NVIDIA_VISIBLE_DEVICES` 环境变量）
3. 宿主机模型目录（离线推荐）：

   ```text
   /aidata/models/embeddings/Qwen3-Embedding-0.6B/
   /aidata/models/reranker/Qwen3-Reranker-0.6B/
   ```

3. `.env` 中至少确认：

   ```env
   EMBEDDING_MODEL_PATH=/workspace/models/embeddings/Qwen3-Embedding-0.6B
   EMBEDDING_QUERY_PROMPT_NAME=query
   EMBEDDING_TRUST_REMOTE_CODE=true
   EMBEDDING_DEVICE=cuda:0

   RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
   RAG_RERANKER_TRUST_REMOTE_CODE=true
   RAG_RERANKER_DEVICE=cuda:1

   MODELS_APP_NVIDIA_VISIBLE_DEVICES=all
   # 与 vLLM 的 GPU_DEVICE_COUNT 同理；单卡可设为 1 或 all
   # MODELS_APP_GPU_DEVICE_COUNT=all
```

单卡 3090 同时跑 vLLM 时，嵌入/重排建议同卡：`EMBEDDING_DEVICE=cuda:0`、`RAG_RERANKER_DEVICE=cuda:0`（勿写 `cuda:1`）。

## 启动

在 **`app/app-deploy`** 目录执行：

```bash
# 主栈（嵌入/重排 GPU）
docker compose -f docker-nvidia/docker-compose-nvidia.yml up -d --build

# 可选：小模型 GPU（端口 APP_PORT_GPU，默认 8081）
docker compose -f docker-nvidia/docker-compose-nvidia.yml --profile small-model-gpu up -d models-app-gpu
```

## 验证

```bash
curl -s "http://127.0.0.1:${APP_PORT:-8083}/health/"
docker exec models-app nvidia-smi
docker compose -f docker-nvidia/docker-compose-nvidia.yml logs models-app | grep -E "EmbeddingService|CrossEncoder reranker"
```

期望日志含 `embedding_dim=1024`、`device=cuda:...`（嵌入与重排设备以 `.env` 为准）。

## 与 CPU / 沐曦栈对比

| 栈 | Compose | 嵌入/重排 |
|----|---------|-----------|
| CPU | `docker-compose.yml` | CPU only |
| **英伟达 GPU** | **`docker-nvidia/docker-compose-nvidia.yml`** | **GPU** |
| 沐曦 GPU | `docker-mx/docker-compose-mx.yml` | GPU（Metax） |

## 索引迁移（BGE → Qwen3）

向量维度 **512 → 1024** 时须：

1. 递增 `RAG_ES_INDEX_VERSION`
2. 保持 `RAG_ES_AUTO_MIGRATE_ON_START=true` 或调用 migration API
3. **全量 re-ingest** 知识库

## 更多说明

- 完整参数与排障：`../README.md`「部署形态选择」
- 快速上线：`../README-simple-deploy.md` §3.2
- 离线外挂服务：`../README-external-services-lan-deploy.md`
