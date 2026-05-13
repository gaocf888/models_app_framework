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

**DOCX→PDF**：`Dockerfile.gpu.mthreads` 构建阶段会在 yum/dnf/apt 上尝试安装 LibreOffice（`libreoffice-headless` 等）。若构建失败或运行时仍报 `DOCX_TO_PDF_UNAVAILABLE`，说明底镜像源内无对应包，需在 Dockerfile 中换可用 repo 或现场预装 `soffice` 后再构建侧车镜像。

### 沐曦硬件：曦思 **N260** 与当前编排是否一致

| 维度 | 结论 |
|------|------|
| **产品线** | N260 属沐曦 **曦思 N 系列**；仓库内 README / 飞桨官方安装页常以 **曦云 C500** 命名，指的是「MACA 上的飞桨插件」示例，**不是**只支持 C500、不支持 N260。 |
| **与本仓库 Compose/Dockerfile** | **一致**：均要求宿主机为沐曦 **MACA** 环境、`MX_VISIBLE_DEVICES`、容器内安装 **`paddlepaddle` + `paddle-metax-gpu`**（默认 **nightly + `--pre`**）。与卡型号是 C500 还是 **N260** 无冲突——由 **MACA 驱动/运行时** 识别具体 GPU。 |
| **建议自检** | 宿主机 `mx-smi`（或厂商等价命令）能识别 N260；`PADDLE_LAYOUT_MT_BASE_IMAGE` 使用与现场 **MACA 主版本** 匹配的官方镜像（与 `vllm-deploy` 同源即可）；若飞桨 nightly wheel 与现场 Python/GLIBC 不兼容，再按飞桨文档改用源码编译或厂商定制 wheel。 |

**说明**：沐曦 GPU 路径在镜像构建阶段使用 **`PADDLE_LAYOUT_METAX_CHANNEL=nightly`**（默认）时执行：`pip install --pre paddlepaddle -i .../nightly/cpu/` 与 `pip install --pre paddle-metax-gpu -i .../nightly/maca/`（与[飞桨沐曦安装文档](https://www.paddlepaddle.org.cn/documentation/docs/zh/hardware_support/metax/install_cn.html)一致）。`stable/maca` 常无 `paddle-metax-gpu` 的 pip 包。若仅需先跑通 HTTP 链路，可设 `PADDLE_LAYOUT_METAX_PADDLE_GPU=0` 使用 paddle 2.6 CPU 包（无沐曦 GPU 加速）。

## 3. 与主应用对接

主应用环境变量（见 `app/app-deploy/.env.example`）：

- `INSPECT_EXTRACT_V0_LAYOUT_OCR_ENDPOINT`：侧车根地址。**主应用跑在 Docker 内**时须写 `http://paddleocr-layout-api:8000`（服务名 + 容器内端口）；`8010` 仅为宿主机映射。**须与侧车在同一 Docker 网络**：侧车 compose 会创建 `PADDLE_LAYOUT_NETWORK_NAME`（默认 `paddle-layout-stack`）；`app/app-deploy/docker-compose.yml` 已将 `models-app` 挂到同名 external 网络 `PADDLE_LAYOUT_DOCKER_NETWORK`，否则主应用解析 `paddleocr-layout-api` 会报 **Name or service not known**。仅当主应用与侧车都在宿主机进程、侧车映射 `8010→8000` 时，才可用 `http://127.0.0.1:8010`。

## 4. OpenAPI 契约

- `GET /health`：存活探针；返回 `status`、`engine`、`version`。
- `POST /v1/layout-ocr`：`multipart/form-data` 上传 **PDF、DOC/DOCX、PNG/JPEG**。DOC/DOCX 在容器内由 **LibreOffice headless** 转为 PDF 后再 `pdf2image` + **行级 PaddleOCR**（`blocks`）+ 可选 **PP-Structure 表格**（`tables`）。`Dockerfile.cpu` / `Dockerfile.gpu.nvidia` 已装 `libreoffice-writer-nogui`；沐曦 yum 底镜像需自行保证 `soffice` 可用。Query `max_pages` 限制 PDF 渲染页数。
- 响应 JSON：`engine_version`、`ocr_engine`、`layout_engine`、`pages`、`blocks`、**`tables`**、`metrics`。
  - **`blocks`**：检测框 + 行级识别文本（与原先一致）。
  - **`tables`**：版面中识别为 `table` 的区域。每项含 `table_id`、`page_no`、`bbox`（页内像素框）、`html`（表格结构 HTML）、**`rows`**（由 HTML 解析的二维字符串矩阵）、`n_rows` / `n_cols`、`cell_bbox`。未检出表格时为空数组。
  - **`PADDLE_LAYOUT_ENABLE_TABLE_STRUCTURE`**（默认 `1`）：`0`/`false` 时关闭表格分支（仅行级 OCR，更快）。PP-Structure 首请求可能下载版面/表格权重，明显慢于仅 OCR。

## 5. 运维 Runbook（摘要）

| 场景 | 处理 |
|------|------|
| 容器 OOM | 调低 `PADDLE_LAYOUT_MEM_LIMIT` 或缩小 `max_pages`；GPU 版可换更小模型或降低并发 |
| 首请求极慢 | PaddleOCR 懒加载；**PP-Structure 首次还会拉版面/表格模型**。预热可各打一次小图 `/v1/layout-ocr`；若无需表格可设 `PADDLE_LAYOUT_ENABLE_TABLE_STRUCTURE=0` |
| 大文件拒绝 | 调 `PADDLE_LAYOUT_MAX_UPLOAD_MB` 与主应用 `INSPECT_EXTRACT_V0_LAYOUT_OCR_MAX_UPLOAD_MB` 对齐 |
| 英伟达 GPU 不可用 | 检查 `nvidia-smi` 与 compose 是否带 `-f docker-compose.gpu.nvidia.yml`；环境变量 `NVIDIA_VISIBLE_DEVICES` |
| 构建 **`exec format error`**（`/bin/sh`） | **底镜像 CPU 架构与宿主机不一致**（例如在 x86 上用了 **`-arm64`** tag）。**处理**：使用默认 **`kylinv11-amd64`**，或按架构改 `PADDLE_LAYOUT_MT_BASE_IMAGE`。 |
| 沐曦 `paddle-metax-gpu` pip 报 **No matching distribution** | 多见于 **Python 3.12** 或 **maca 索引无对应 cp**。**处理**：使用预装飞桨的 **`maca/paddle:`** 或 **`paddle-metax`** 官方底镜像（构建会**跳过**飞桨 pip）；或设 **`METAX_PADDLE_GPU=0`** 仅 CPU paddle。 |
| DOCX 返回 **503 / DOCX_TO_PDF_UNAVAILABLE** | 侧车内 **`soffice`/`libreoffice` 不在 PATH**（日志与响应里 `DOCX_TO_PDF_UNAVAILABLE`）。**常见原因**：**麒麟 Kylin 默认 yum/dnf 源里没有** Fedora 式包名 `libreoffice-headless` 等，`Dockerfile.gpu.mthreads` 构建阶段会尽力安装但**可能装不上**，镜像仍能构建，**运行时** docx 转 PDF 失败。**处理**（择一）：① 换 **`Dockerfile.cpu` / `Dockerfile.gpu.nvidia`**（apt 已装 `libreoffice-writer-nogui`）跑侧车；② **构建参数**使用**非麒麟官方源**安装 LO（见 **§5.2**）；③ 在 Dockerfile 中 **COPY 官方 LibreOffice 便携包** 并 `ENV PATH=/opt/libreoffice/program:$PATH`；④ 主应用 **`INSPECT_EXTRACT_V0_DOCX_USE_LAYOUT_OCR=false`**，docx 不走侧车。 |
| 回滚 | 固定镜像 tag；主应用关闭 `INSPECT_EXTRACT_V0_ENABLED` 即切断调用 |

### 5.1 麒麟（沐曦镜像）与 LibreOffice 说明

- **构建成功 ≠ 已装 LO**：`Dockerfile.gpu.mthreads` 在 yum/dnf 上若源内无匹配包，会打印 **WARN** 并继续构建；是否具备 `soffice` 以**容器内 `command -v soffice`** 为准。  
- **验证**：`docker compose exec paddleocr-layout-api sh -lc 'command -v soffice || command -v libreoffice; ls /usr/lib64/libreoffice/program/soffice 2>/dev/null'` 无输出即未安装。  
- **与表格 PP-Structure 无关**：缺 LO 时 docx 在转 PDF 之前即失败；表格识别是后续步骤。

### 5.2 使用国内 / 第三方源安装 LibreOffice（`Dockerfile.gpu.mthreads` 构建参数）

可在 **`docker compose -f docker-compose.gpu.mthreads.yml build`** 时通过环境变量传入（已映射到 `build.args`），或 `docker build --build-arg ...`：

| 构建参数 / compose 变量 | 含义 |
|-------------------------|------|
| `PADDLE_LAYOUT_LO_BUILTIN_CHINA_EPEL` | **`1`（默认）**：未设置 `LO_REPO_URL` 时，将镜像内 **`docker/epel-china-el7.repo` / `epel-china-el8.repo`**（阿里云 EPEL 镜像）复制到 `/etc/yum.repos.d/paddle-layout-builtin-epel.repo`，按 `rpm -E '%{rhel}'` 在 **7** 与其余（按 **8** 源）间选择；**`0`** 则关闭，仅用麒麟官方源或下方自定义 URL。**注意**：底镜像若为 RHEL 9 等而仍用 el8 EPEL，可能与 glibc/RPM 依赖不完全一致，此时应关闭内置或提供匹配的 `LO_REPO_URL`。 |
| `PADDLE_LAYOUT_LO_REPO_URL` | 可 `curl` 的 **`.repo` 文件 URL**（写入 `/etc/yum.repos.d/paddle-layout-lo.repo` 后 `yum/dnf makecache`）。**若设置，则不再启用内置阿里云 EPEL**（由该文件全权提供额外源）。用于接入贵司制品库、Rocky/Alma 兼容源、厂商镜像站等（**需自行评估与麒麟 glibc 的 RPM 兼容性**）。 |
| `PADDLE_LAYOUT_LO_YUM_PACKAGES` | **空格分隔**的包名；在追加 repo / 内置 EPEL 之后执行 **一次** `yum install -y` / `dnf install -y` 装齐（默认：`libreoffice-headless libreoffice-langpack-zh-Hans`）。 |
| `PADDLE_LAYOUT_LO_RPM_URLS` | **空格分隔**的 RPM **直链**，依次 `curl` 下载后 `rpm -ivh --nodeps`，失败则再试 `yum/dnf install` 该本地文件。适合离线制品单包或依赖链已手工排好。 |
| `PADDLE_LAYOUT_LO_YUM_NOGPGCHECK` | 设为 **`1`** 时，`yum`/`dnf install` 追加 **`--nogpgcheck`**（仅当第三方 repo 无签名且现场明确接受风险时使用）。 |

`.env` 示例片段（勿提交真实内网 URL 到公仓）：

```bash
PADDLE_LAYOUT_LO_BUILTIN_CHINA_EPEL=1
# 若需完全自建源，可设 BUILTIN=0 并指定：
# PADDLE_LAYOUT_LO_REPO_URL=https://artifacts.example.com/yum/libreoffice-el8.repo
PADDLE_LAYOUT_LO_YUM_PACKAGES=libreoffice-headless libreoffice-langpack-zh-Hans
# 或仅用 RPM：
# PADDLE_LAYOUT_LO_RPM_URLS=https://artifacts.example.com/rpms/libreoffice-core-7.x.rpm
```

- 发布前对镜像执行 **Trivy / Grype** 等 CVE 扫描，将报告结论与基线镜像版本记录在发布 PR。
- 生产环境建议仅集群内网访问该服务；**沐曦 compose 使用 privileged + 全量 `/dev`**，仅部署在受信节点，并按厂商安全基线收敛设备挂载（若现场规范要求）。

## 7. 离线构建

与 MinerU 类似，构建阶段可配置 `PIP_INDEX_URL`；Paddle 权重可挂载至 `PADDLE_LAYOUT_MODELS_HOST_PATH` 对应容器内路径（见 `docker-compose` 卷映射）。英伟达 GPU 的 `paddlepaddle-gpu` 默认从飞桨 `cu118` 索引拉取，离线环境需预先 `docker pull` 构建机可访问的基础镜像与 wheel 缓存策略。
