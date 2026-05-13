# PaddleOCR 版面 + OCR 侧车（检修结构化提取 V0）

本目录为 **检修报告结构化提取 V0** 的独立 HTTP 侧车（FastAPI），与 `mineru-deploy/`、`vllm-deploy/` 同级，运维习惯对齐 MinerU / vLLM 部署说明。

- **CPU**：`Dockerfile.cpu` + `docker-compose.yml` / `docker-compose.cpu.yml`
- **GPU · 英伟达**：`Dockerfile.gpu.nvidia` + `docker-compose.gpu.nvidia.yml`（`gpus: all`，与 `mineru-deploy/docker-compose.gpu.yml` 一致）
- **GPU · 沐曦（Metax / MACA）**：`Dockerfile.gpu.mthreads` + `docker-compose.gpu.mthreads.yml`（`privileged: true` + 挂载 `/dev` + `MX_VISIBLE_DEVICES`，与 `vllm-deploy/docker/docker-compose.mthreads.yml` 一致）

Python 依赖拆分为 `service/requirements-base.txt`（无 Paddle）、`requirements-paddle-cpu.txt`（CPU 镜像用）、`requirements-paddleocr.txt`（PaddleOCR，须在 Paddle 安装之后）；`requirements.txt` 为 CPU 聚合入口。

## 1. 目录结构

- `Dockerfile.cpu`：Python 3.11 bookworm + CPU Paddle
- `Dockerfile.gpu.nvidia`：`nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04` + Python 3.11 + `paddlepaddle-gpu`（cu118 官方索引）
- `Dockerfile.gpu.mthreads`：`ARG BASE_IMAGE` 默认沐曦 MACA 推理栈镜像 + `paddlepaddle` + `paddle-metax-gpu`（可 `--build-arg METAX_PADDLE_GPU=0` 退回 CPU 版 paddle 2.6）
- `docker-compose.yml` / `docker-compose.cpu.yml`：CPU 编排
- `docker-compose.gpu.nvidia.yml` / `docker-compose.gpu.mthreads.yml`：GPU 编排覆盖层（须与 `docker-compose.yml` **双文件**合并启动）
- `docker/entrypoint.sh`：初始化缓存与输出目录
- `service/main.py`：FastAPI（`/health`、`/v1/layout-ocr`、`/docs`）；`PADDLE_LAYOUT_USE_GPU=1` 时 `PaddleOCR(..., use_gpu=True)`
- `.env.example`：含 CPU / NVIDIA / 沐曦相关变量说明

## 2. 快速启动

### CPU（默认）

```bash
cd paddleocr-layout-deploy
cp .env.example .env
# 编辑 .env：设置 PADDLE_LAYOUT_MODELS_HOST_PATH / PADDLE_LAYOUT_IO_HOST_PATH
docker compose -f docker-compose.cpu.yml up -d --build
curl -sS http://127.0.0.1:8010/health
```

### GPU · 英伟达

前置：宿主机已安装 NVIDIA 驱动与 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)，`docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi` 正常。

```bash
cd paddleocr-layout-deploy
cp .env.example .env
# 按需修改 PADDLE_LAYOUT_NV_*、PADDLE_LAYOUT_NVIDIA_VISIBLE_DEVICES、CUDA_VISIBLE_DEVICES
docker compose -f docker-compose.yml -f docker-compose.gpu.nvidia.yml up -d --build
curl -sS http://127.0.0.1:8010/health
```

### GPU · 沐曦（Metax）

前置：已具备厂商提供的 **MACA 基础镜像**（默认标签与 `vllm-deploy/.env.example` 中 `BASE_IMAGE` 可保持一致），且现场允许 `privileged` + `/dev` 透传（与 `vllm-deploy/docker/docker-compose.mthreads.yml` 相同安全边界）。

```bash
cd paddleocr-layout-deploy
cp .env.example .env
# 设置 PADDLE_LAYOUT_MT_BASE_IMAGE、MX_VISIBLE_DEVICES、PADDLE_LAYOUT_METAX_* 等
docker compose -f docker-compose.yml -f docker-compose.gpu.mthreads.yml up -d --build
curl -sS http://127.0.0.1:8010/health
```

**说明**：沐曦路径默认按飞桨文档安装 `paddlepaddle==3.3.0`（cpu 索引包）+ `paddle-metax-gpu==3.3.0`（maca 索引）。若与现场 Python 版本或 wheel 不匹配，请调整构建参数或改用 `METAX_PADDLE_GPU=0` 仅装 paddle 2.6 CPU 包（无沐曦 GPU 加速，便于先打通链路）。

## 3. 与主应用对接

主应用环境变量（见 `app/app-deploy/.env.example`）：

- `INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT`：侧车根地址，例如 `http://127.0.0.1:8010` 或 compose 内 `http://paddleocr-layout-api:8000`。

## 4. OpenAPI 契约

- `GET /health`：存活探针；返回 `status`、`engine`、`version`。
- `POST /v1/layout-ocr`：`multipart/form-data` 上传 PDF 或图像；Query `max_pages` 限制 PDF 渲染页数。
- 响应 JSON 含 `engine_version`、`ocr_engine`、`layout_engine`、`pages`、`blocks`；`tables` 当前为空数组（PP-Structure 表结构后续接入）。

## 5. 运维 Runbook（摘要）

| 场景 | 处理 |
|------|------|
| 容器 OOM | 调低 `PADDLE_LAYOUT_MEM_LIMIT` 或缩小 `max_pages`；GPU 版可换更小模型或降低并发 |
| 首请求极慢 | PaddleOCR 懒加载；预热可打一次小图 `/v1/layout-ocr` |
| 大文件拒绝 | 调 `PADDLE_LAYOUT_MAX_UPLOAD_MB` 与主应用 `INSPECT_EXTRACT_V0_LAYOUT_OCR_MAX_UPLOAD_MB` 对齐 |
| 英伟达 GPU 不可用 | 检查 `nvidia-smi` 与 compose 是否带 `-f docker-compose.gpu.nvidia.yml`；环境变量 `NVIDIA_VISIBLE_DEVICES` |
| 沐曦容器起不来 | 核对 `BASE_IMAGE` 拉取权限；`paddle-metax-gpu` 版本与飞桨文档一致；必要时 `METAX_PADDLE_GPU=0` 先跑 CPU paddle |
| 回滚 | 固定镜像 tag；主应用关闭 `INSPECT_EXTRACT_V0_ENABLED` 即切断调用 |

## 6. 安全与 CVE

- 发布前对镜像执行 **Trivy / Grype** 等 CVE 扫描，将报告结论与基线镜像版本记录在发布 PR。
- 生产环境建议仅集群内网访问该服务；**沐曦 compose 使用 privileged + 全量 `/dev`**，仅部署在受信节点，并按厂商安全基线收敛设备挂载（若现场规范要求）。

## 7. 离线构建

与 MinerU 类似，构建阶段可配置 `PIP_INDEX_URL`；Paddle 权重可挂载至 `PADDLE_LAYOUT_MODELS_HOST_PATH` 对应容器内路径（见 `docker-compose` 卷映射）。英伟达 GPU 的 `paddlepaddle-gpu` 默认从飞桨 `cu118` 索引拉取，离线环境需预先 `docker pull` 构建机可访问的基础镜像与 wheel 缓存策略。
