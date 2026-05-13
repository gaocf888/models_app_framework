# PaddleOCR 版面 + OCR 侧车（检修结构化提取 V0）

本目录为 **检修报告结构化提取 V0** 的独立 HTTP 侧车（FastAPI），与 `mineru-deploy/`、`vllm-deploy/` 同级，运维习惯对齐 MinerU / vLLM 部署说明。

- **CPU**：`Dockerfile.cpu` + `docker-compose.yml` / `docker-compose.cpu.yml`
- **GPU · 英伟达**：`Dockerfile.gpu.nvidia` + `docker-compose.gpu.nvidia.yml`（`gpus: all`，与 `mineru-deploy/docker-compose.gpu.yml` 一致）
- **GPU · 沐曦（Metax / MACA）**：`Dockerfile.gpu.mthreads` + `docker-compose.gpu.mthreads.yml`（`privileged: true` + 挂载 `/dev` + `MX_VISIBLE_DEVICES`，与 `vllm-deploy/docker/docker-compose.mthreads.yml` 一致）。**曦思 N260** 与曦云 C 系列同属沐曦 **MACA** 栈，本路径依赖 MACA 运行时 + 飞桨 `paddle-metax-gpu`（与飞桨文档「曦云 C500」示例为同一软件路径；具体算力卡型号由宿主机驱动/MACA 版本决定，见下文「沐曦硬件」）。

Python 依赖拆分为 `service/requirements-base.txt`（无 Paddle）、`requirements-paddle-cpu.txt`（CPU 镜像用）、`requirements-paddleocr.txt`（PaddleOCR，须在 Paddle 安装之后）；`requirements.txt` 为 CPU 聚合入口。

## 1. 目录结构

- `Dockerfile.cpu`：Python 3.11 bookworm + CPU Paddle
- `Dockerfile.gpu.nvidia`：`nvidia/cuda:11.8.0-cudnn8-runtime-ubuntu22.04` + Python 3.11 + `paddlepaddle-gpu`（cu118 官方索引）
- `Dockerfile.gpu.mthreads`：`ARG BASE_IMAGE` 默认 **沐曦 `maca/paddle:2.6.0-…-py310-kylinv11-amd64`**（麒麟 Kylin V11 + x86_64；`docker login cr.metax-tech.com` 后拉取；路径含 `maca/paddle:` 时构建**跳过**飞桨/沐曦插件 pip）。ARM64 现场请覆盖为仓库内 **arm64** 对应 tag。
- `docker-compose.yml` / `docker-compose.cpu.yml`：CPU 编排（`Dockerfile.cpu`）
- `docker-compose.gpu.nvidia.yml` / `docker-compose.gpu.mthreads.yml`：**独立完整** GPU 编排（各自含 `Dockerfile.gpu.*`、端口、卷、健康检查与网络），**不要**再与 `docker-compose.yml` 双文件合并，以免误读或合并顺序导致仍指向 CPU Dockerfile
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
docker compose -f docker-compose.gpu.nvidia.yml up -d --build
curl -sS http://127.0.0.1:8010/health
```

### GPU · 沐曦（Metax）

前置：宿主机为 **麒麟 + 沐曦 MACA**；默认底镜像为 **`maca/paddle:2.6.0-…-kylinv11-amd64`**（**x86_64**），已含飞桨与 MACA，构建不再 pip `paddle-metax-gpu`。若现场为 **ARM64** 麒麟，请将 `PADDLE_LAYOUT_MT_BASE_IMAGE` 改为沐曦 **`…-arm64`** 等 tag（与宿主机 CPU 架构一致，否则构建会出现 **exec format error**）。若坚持用 **`vllm-metax`（Python 3.12）**，请设 **`PADDLE_LAYOUT_METAX_PADDLE_GPU=0`**。现场允许 `privileged` + `/dev` 透传。

```bash
cd paddleocr-layout-deploy
cp .env.example .env
# 设置 PADDLE_LAYOUT_MT_BASE_IMAGE、MX_VISIBLE_DEVICES、PADDLE_LAYOUT_METAX_* 等
docker compose -f docker-compose.gpu.mthreads.yml up -d --build
curl -sS http://127.0.0.1:8010/health
```

### 沐曦硬件：曦思 **N260** 与当前编排是否一致

| 维度 | 结论 |
|------|------|
| **产品线** | N260 属沐曦 **曦思 N 系列**；仓库内 README / 飞桨官方安装页常以 **曦云 C500** 命名，指的是「MACA 上的飞桨插件」示例，**不是**只支持 C500、不支持 N260。 |
| **与本仓库 Compose/Dockerfile** | **一致**：均要求宿主机为沐曦 **MACA** 环境、`MX_VISIBLE_DEVICES`、容器内安装 **`paddlepaddle` + `paddle-metax-gpu`**（默认 **nightly + `--pre`**）。与卡型号是 C500 还是 **N260** 无冲突——由 **MACA 驱动/运行时** 识别具体 GPU。 |
| **建议自检** | 宿主机 `mx-smi`（或厂商等价命令）能识别 N260；`PADDLE_LAYOUT_MT_BASE_IMAGE` 使用与现场 **MACA 主版本** 匹配的官方镜像（与 `vllm-deploy` 同源即可）；若飞桨 nightly wheel 与现场 Python/GLIBC 不兼容，再按飞桨文档改用源码编译或厂商定制 wheel。 |

**说明**：沐曦 GPU 路径在镜像构建阶段使用 **`PADDLE_LAYOUT_METAX_CHANNEL=nightly`**（默认）时执行：`pip install --pre paddlepaddle -i .../nightly/cpu/` 与 `pip install --pre paddle-metax-gpu -i .../nightly/maca/`（与[飞桨沐曦安装文档](https://www.paddlepaddle.org.cn/documentation/docs/zh/hardware_support/metax/install_cn.html)一致）。`stable/maca` 常无 `paddle-metax-gpu` 的 pip 包。若仅需先跑通 HTTP 链路，可设 `PADDLE_LAYOUT_METAX_PADDLE_GPU=0` 使用 paddle 2.6 CPU 包（无沐曦 GPU 加速）。

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
| 构建 **`exec format error`**（`/bin/sh`） | **底镜像 CPU 架构与宿主机不一致**（例如在 x86 上用了 **`-arm64`** tag）。**处理**：使用默认 **`kylinv11-amd64`**，或按架构改 `PADDLE_LAYOUT_MT_BASE_IMAGE`。 |
| 沐曦 `paddle-metax-gpu` pip 报 **No matching distribution** | 多见于 **Python 3.12** 或 **maca 索引无对应 cp**。**处理**：使用预装飞桨的 **`maca/paddle:`** 或 **`paddle-metax`** 官方底镜像（构建会**跳过**飞桨 pip）；或设 **`METAX_PADDLE_GPU=0`** 仅 CPU paddle。 |
| 回滚 | 固定镜像 tag；主应用关闭 `INSPECT_EXTRACT_V0_ENABLED` 即切断调用 |

## 6. 安全与 CVE

- 发布前对镜像执行 **Trivy / Grype** 等 CVE 扫描，将报告结论与基线镜像版本记录在发布 PR。
- 生产环境建议仅集群内网访问该服务；**沐曦 compose 使用 privileged + 全量 `/dev`**，仅部署在受信节点，并按厂商安全基线收敛设备挂载（若现场规范要求）。

## 7. 离线构建

与 MinerU 类似，构建阶段可配置 `PIP_INDEX_URL`；Paddle 权重可挂载至 `PADDLE_LAYOUT_MODELS_HOST_PATH` 对应容器内路径（见 `docker-compose` 卷映射）。英伟达 GPU 的 `paddlepaddle-gpu` 默认从飞桨 `cu118` 索引拉取，离线环境需预先 `docker pull` 构建机可访问的基础镜像与 wheel 缓存策略。
