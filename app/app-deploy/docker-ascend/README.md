# 昇腾 GPU 应用栈（`docker-ascend/`）

本目录提供 **华为昇腾 Ascend（Atlas 300I Duo）** 环境下的 `models-app` 部署，与上级目录的 **CPU 栈**、**英伟达**（`docker-nvidia/`）、**沐曦**（`docker-mx/`）并列。

## 适用场景

- 宿主机为 **Atlas 300I Duo**，已安装 **NPU 驱动 26.1.1 / 固件 9.0.0.9.220** 与 **Ascend Docker Runtime 26.1.0**
- 希望 **Qwen3 嵌入/重排** 跑在 NPU（`EMBEDDING_DEVICE` / `RAG_RERANKER_DEVICE`）
- 底座镜像默认：`quay.io/ascend/vllm-ascend:v0.23.0-310p`（与 vLLM / MinerU 同底座；建议 `.env` 写 `BASE_IMAGE`）

## 文件说明

| 文件 | 说明 |
|------|------|
| `Dockerfile-ascend` | `FROM` 昇腾底座 + 业务 requirements（保护 `torch_npu`） |
| `docker-compose-ascend.yml` | Redis、MinIO、`models-app`（NPU） |

`.env` 使用 **`app/app-deploy/.env`**，compose 通过 `env_file: ../.env` 引用。

## 业务源码 mount（离线迭代）

- 默认宿主机：`/opt/deploy/models_app_framework/app` → 容器 `/workspace/app`
- 默认宿主机：`/opt/deploy/models_app_framework/configs` → 容器 `/workspace/configs`
- **有网机 build 镜像**（含 pip 与 COPY）；**离线机** sync 源码后 `docker compose restart models-app`
- 首次：在宿主机准备 `/opt/deploy/models_app_framework/{app,configs}` 并同步代码/配置后 `docker compose up -d`

## 四卡切分（与方案 §5 一致）

| 栈 | `ASCEND_RT_VISIBLE_DEVICES` |
|----|-----------------------------|
| vLLM | `0,1,2,3` |
| **本栈 models-app** | **`4,5`** |
| MinerU | `6` |

容器内相对设备：`EMBEDDING_DEVICE=npu:0`、`RAG_RERANKER_DEVICE=npu:1`。

## 启动

```bash
cd app/app-deploy
cp .env.example .env   # 首次
# 编辑：EMBEDDING_DEVICE / RAG_RERANKER_DEVICE / ASCEND_RT_VISIBLE_DEVICES 等

docker network create paddle-layout-stack 2>/dev/null || true
docker network create face-milvus-stack 2>/dev/null || true

docker compose -f docker-ascend/docker-compose-ascend.yml up -d --build
```

详细宿主机步骤见 `docs/基础环境及部署/华为Atlas300IDuo基础环境及应用部署方案.md`。
