# 英伟达 GPU 应用栈（`docker-nvidia/`）

本目录提供 **英伟达 GPU** 环境下的 `models-app` 部署，与上级目录的 **CPU 栈**（`docker-compose.yml`）及 **沐曦 GPU 栈**（`docker-mx/`）并列。

主服务使用 `Dockerfile-nvidia`（CUDA 版 PyTorch），让 RAG 重排、嵌入、轻量意图 LLM 走 GPU。小模型 YOLO 仍用上级 `Dockerfile.small-model-gpu`，经 `--profile small-model-gpu` 启动。

## 前置条件

1. 宿主机 `nvidia-smi` 正常。
2. 已安装 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。
3. 外部依赖已启动且网络存在：EasySearch（`ai-stack`）、vLLM（`docker_vllm-network`）、可选 MinerU（`mineru-stack`）。
4. 业务配置使用上级 `app/app-deploy/.env`（由 `.env.example` 复制）。

## 启动

```bash
cd app/app-deploy
cp .env.example .env          # 首次
# 按下文「.env 要点」改完后再复制
cp .env docker-nvidia/.env
cd docker-nvidia
docker compose --env-file .env -f docker-compose-nvidia.yml up -d --build
```

小模型 GPU（`/small-model/*`）：

```bash
docker compose --env-file .env -f docker-compose-nvidia.yml --profile small-model-gpu up -d --build
```

## 验证

```bash
docker exec models-app python -c "import torch; print(torch.cuda.is_available(), torch.cuda.device_count())"
docker compose -f docker-compose-nvidia.yml logs models-app | grep -E "EmbeddingService|CrossEncoder reranker"
```

`torch.cuda.is_available()` 应为 `True`。重排日志中 `device=` 应为 `cuda` / `cuda:0` / `cuda:1`，而不是 `cpu`。

## 与其它栈对照

| 环境 | Compose | models-app 推理后端 |
|------|---------|---------------------|
| CPU | `../docker-compose.yml` | CPU PyTorch |
| **英伟达 GPU** | **`docker-compose-nvidia.yml`** | **CUDA PyTorch** |
| 沐曦 GPU | `../docker-mx/docker-compose-mx.yml` | Metax / MACA |

`.env` 中需要核对的项见上级 `README-simple-deploy.md` 与 `.env.example` 注释（分卡、`RAG_RERANKER_DEVICE`、网络名等）。
