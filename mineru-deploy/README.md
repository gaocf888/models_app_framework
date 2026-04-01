# MinerU 独立部署栈（`mineru-deploy`）

与 **`rag_db-deploy`**、**`vllm-deploy`** 相同，本目录为 **外挂独立服务**：不在 `app/app-deploy` 内嵌 MinerU，由运维在本目录单独执行 `docker compose` 启停。

**本文档范围**：**`mineru-deploy`** 的镜像、配置、网络、卷、离线模型，以及 **`models-app` 如何通过网络与共享目录调用 MinerU**（含端口说明）。`models-app` 内 **HTTP 客户端与 RAG 编排代码** 见 `docs/MinerU-RAG-技术方案与实施清单.md` 与 `app/` 源码。

模型下载地址：(使用git lfs install)
git clone https://www.modelscope.cn/microsoft/layoutlmv3-base.git



---

## 0. 前置条件

| 项 | 说明 |
|----|------|
| Docker / Docker Compose | 建议 **Docker Compose v2**（`docker compose` 子命令） |
| GPU 模式（可选） | **NVIDIA 驱动** + **NVIDIA Container Toolkit**；`docker run --rm --gpus all nvidia/cuda:12.0.0-base-ubuntu22.04 nvidia-smi` 可验证 |
| 磁盘 | 离线模型体积大，**`MINERU_MODELS_HOST_PATH`** 与 **`MINERU_IO_HOST_PATH`** 所在分区需预留充足空间 |
| 镜像 | **必须**从内网镜像仓或离线包加载 **`.env` 中的 `MINERU_IMAGE`**；本仓库 **不提供** 官方镜像标签绑定（随 MinerU 发版变化） |

---

## 1. 目录与文件

| 路径 | 说明 |
|------|------|
| `docker-compose.yml` | **默认 CPU**，`cpus` / `mem_limit` 限制资源（`docker compose up` 即生效） |
| `docker-compose.gpu.yml` | **GPU 叠加**，与上一文件 **同时** `-f` 指定 |
| `.env.example` | 环境变量模板；复制为 **`.env`** 后修改 |
| `.gitignore` | 忽略 `.env` 与真实 `config/mineru-tools.json`（若含内网路径） |
| `config/mineru-tools.json.example` | 离线 **`models-dir`** 占位示例；复制为 **`config/mineru-tools.json`** 并按 **所选 MinerU 版本官方文档** 补全字段（不同版本 schema 可能不同，**不可**照抄视为最终配置） |

---

## 2. 首次部署检查清单（仅 MinerU 栈）

按顺序执行，便于排障：

1. **复制配置**  
   `cp .env.example .env`  
   `cp config/mineru-tools.json.example config/mineru-tools.json`
2. **编辑 `.env`**  
   - **`MINERU_IMAGE`**：改为内网可用镜像（**必填**）。  
   - **`MINERU_MODELS_HOST_PATH`**、**`MINERU_IO_HOST_PATH`**：改为本机**已存在**的绝对路径（可先 `mkdir -p`）。  
   - **`MINERU_TOOLS_CONFIG_HOST_PATH`**：默认 `./config/mineru-tools.json`；若改路径，compose 中挂载需一致。
3. **编辑 `config/mineru-tools.json`**  
   按官方文档填写 **`models-dir`** 等；容器内模型根为 **`/models`**，故 JSON 中路径应形如 **`/models/...`**。
4. **放入离线权重**  
   将模型文件放到上一步 `models-dir` 指向的**宿主机**目录（与 `/models` 挂载对应）。
5. **选择 CPU 或 GPU 启动**（见 §3）。
6. **验证**（见 §6）。

---

## 3. CPU / GPU 模式切换

| 模式 | `.env` 建议 | 命令 |
|------|-------------|------|
| **CPU（默认）** | `MINERU_DEVICE_MODE=cpu` | `docker compose --env-file .env up -d` |
| **GPU** | `MINERU_DEVICE_MODE=gpu`，并设置 `MINERU_NVIDIA_VISIBLE_DEVICES`（如 `0` 或 `1`） | `docker compose --env-file .env -f docker-compose.yml -f docker-compose.gpu.yml up -d` |

**重要**

- **仅改 `MINERU_DEVICE_MODE=gpu` 而不叠加 `docker-compose.gpu.yml`，不会为容器分配 GPU。**
- GPU 模式请勿与 **vLLM** 默认争抢同一张生产卡；生产建议 **独立 GPU** 或 **错时调度**。
- **`command`** 为常见写法（`mineru-api --host 0.0.0.0 --port 8000`）。若容器启动后立即退出，请以 **所选镜像的 Dockerfile / 官方文档** 为准修改 `docker-compose.yml` 中的 `command` 或删除 `command` 使用镜像默认 `ENTRYPOINT`。

---

## 4. 环境变量说明（`.env`）

| 变量 | 必填 | 说明 |
|------|------|------|
| `MINERU_IMAGE` | 是 | 内网镜像名:标签 |
| `MINERU_CONTAINER_NAME` | 否 | 默认 `mineru-api` |
| `MINERU_PORT` | 否 | **仅宿主机**映射：`${MINERU_PORT}:8000`，默认 **8009→8000**；供浏览器/宿主机 `curl` 使用，**不是** `models-app` 容器间访问端口（见 §7） |
| `MINERU_DEVICE_MODE` | 否 | `cpu` / `gpu`（与 compose 调用方式配合） |
| `MINERU_NVIDIA_VISIBLE_DEVICES` | GPU 时建议 | 如 `0`、`1` |
| `MINERU_MODELS_HOST_PATH` | 是 | 宿主机模型目录 → 容器 **`/models`（只读）** |
| `MINERU_IO_HOST_PATH` | 是 | 宿主机 IO 目录 → 容器 **`/io`（读写）**，含 PDF、输出、`HF_HOME` 子目录缓存 |
| `MINERU_TOOLS_CONFIG_HOST_PATH` | 否 | 默认 `./config/mineru-tools.json` |
| `MINERU_MODEL_SOURCE` | 否 | 常见 `local`（以官方文档为准） |
| `MINERU_CPU_LIMIT` | 否 | 容器 CPU 上限（核数，浮点），默认 `4.0` |
| `MINERU_MEM_LIMIT` | 否 | 容器内存上限，默认 `16g`（写法如 `8g`、`32g`） |
| `MINERU_NETWORK_NAME` | 否 | 默认 `mineru-stack`；其他栈通过 **external 网络** 同名接入 |
| `HF_HUB_OFFLINE` / `TRANSFORMERS_OFFLINE` | 否 | 默认 `1`，强化离线；若官方要求联网调试可改为 `0` |

容器内与 compose 写死相关的变量（一般无需在 `.env` 重复）：

- `MINERU_TOOLS_CONFIG_JSON=/config/mineru-tools.json`
- `HF_HOME=/io/.hf_cache`（可写，避免写入只读的 `/models`）

---

## 5. 离线模型：下载与配置

### 5.1 原则

- 在 **可联网环境** 或 **内网制品库** 准备权重，再同步到服务器 **`MINERU_MODELS_HOST_PATH`**。
- 容器内只读挂载 **`/models`**；缓存与中间文件在 **`/io/.hf_cache`**（`HF_HOME`）。

### 5.2 推荐流程

1. 打开 **当前 MinerU 版本** 官方文档，确认 **模型清单、目录结构、`mineru-tools.json` / 环境变量名**（随版本变更）。  
2. **下载方式（示例）**  
   - **ModelScope**：联网机 `git clone` 或使用 ModelScope SDK 下载到本地目录，再 `rsync`/打包到内网服务器。  
   - **HuggingFace**：`huggingface-cli download <repo> --local-dir <dir>` 后整体拷贝入内网；若有 **内网 HF 镜像**，在联网机拉取后固化。  
3. 将文件布局为 **`config/mineru-tools.json`** 中 **`models-dir`** 所指的 **容器路径**对应的宿主机子目录（例如容器内 `/models/pipeline` 对应宿主 `${MINERU_MODELS_HOST_PATH}/pipeline`）。  
4. 复制并编辑配置：  
   `cp config/mineru-tools.json.example config/mineru-tools.json`  
   按官方 schema 补全（示例文件中的 key 仅为占位，**可能与您版本不一致**）。  
5. **离线自检**：断网或阻断出站后启动容器，处理最小 PDF，确认 **无自动下载** 且任务成功。

### 5.3 `config/mineru-tools.json.example` 说明

示例为 **最小占位 JSON**（合法 JSON，无注释）。若运行时报配置解析错误，以 **官方仓库中的示例或生成工具** 为准替换整个文件。

---

## 6. 验证与常用运维命令

**健康检查（依镜像而定）**

- 浏览器访问：`http://<宿主机>:<MINERU_PORT>/docs`（FastAPI 常见）。  
- 若 404，尝试 `/`、`/health`、`/openapi.json` 或查阅镜像说明。

**常用命令**（在 **`mineru-deploy`** 目录）

```bash
# 启动（CPU）
docker compose --env-file .env up -d

# 启动（GPU）
docker compose --env-file .env -f docker-compose.yml -f docker-compose.gpu.yml up -d

# 日志
docker compose --env-file .env logs -f mineru-api

# 停止 / 删除容器（保留卷与宿主数据）
docker compose --env-file .env down

# 查看网络（供 external 接入方核对名称）
docker network ls | grep mineru
```

---

## 7. 端口说明与 `models-app` 如何调用 MinerU

### 7.1 MinerU 容器内 8000 与 vLLM 的 8000 会冲突吗？

**不会（在正确的 Docker 用法下）。**

- **vLLM** 与 **MinerU** 各是一个 **独立容器**，各有自己的 **网络命名空间**。二者在各自容器内监听 **`:8000`**，等同于两台不同机器上各开一个 8000 端口，**互不占用对方端口**。
- 可能冲突的只有 **宿主机端口映射**：若你把 MinerU 和 vLLM 都映射成 **`8000:8000`**，则 **宿主** 上第二个映射会失败。本仓库约定：
  - vLLM 常用宿主端口见 `vllm-deploy`（例如 `VLLM_PORT`）；
  - MinerU 使用 **`MINERU_PORT`（默认 8009）→ 容器 8000**，与 vLLM 宿主端口 **错开** 即可。

**`models-app` 访问 MinerU 时**：应使用 **Docker 服务名 + 容器内端口**，即 **`http://mineru-api:8000`**，**不要**写成 `http://mineru-api:8009`（8009 只在宿主机侧生效）。

### 7.2 端口与地址对照（速查）

| 场景 | 地址或端口 | 说明 |
|------|------------|------|
| **`models-app` → MinerU** | `http://mineru-api:8000` | 与 `mineru-stack` 互通；`mineru-api` 为 `MINERU_CONTAINER_NAME` 默认服务名 |
| **`models-app` → vLLM** | `http://vllm-service:8000/v1`（示例） | 与 vLLM 所在 external 网络互通；**仍是对方容器内端口** |
| **浏览器 / 宿主机调试 MinerU** | `http://<宿主机IP>:<MINERU_PORT>` | 默认 `MINERU_PORT=8009`，映射到 MinerU 容器 **8000** |
| **MinerU 进程监听** | 容器内 **8000** | 由 `docker-compose.yml` 中 `command: ... --port 8000` 与 `ports` 共同约定 |

### 7.3 `models-app` 侧配置（环境变量）

在 **`app/app-deploy/.env`**（由 compose `env_file` 注入容器）中建议：

```env
# 容器间调用 MinerU API：主机名 = compose 服务名，端口 = 对方容器内监听端口（8000）
MINERU_BASE_URL=http://mineru-api:8000
```

并确保 **`app/app-deploy/docker-compose.yml`** 中 `models-app` 已加入 **external 网络**，且名称与 **`MINERU_NETWORK_NAME`**（默认 **`mineru-stack`**）一致；详见该文件内 **`mineru-external`** 与 **`.env.example`** 中 **`MINERU_DOCKER_NETWORK`**。

应用内读取配置见 **`app/core/config.py`** 的 **`MinerUConfig`**（`base_url`、`io_path` 等）。**RAG 摄入里何时调 MinerU** 以路线图文档与后续代码为准；当前仅保证 **网络与 Base URL 语义正确**。

### 7.4 与 MinerU 共享文件目录（PDF / 输出 Markdown）

- **`mineru-deploy/.env`**：`MINERU_IO_HOST_PATH` → 容器 **`/io`**。  
- **`app/app-deploy/.env`**： **`MINERU_IO_HOST_PATH` 必须与上者相同**（同一宿主机路径），使 `models-app` 挂载到 **`/workspace/mineru-io`**（与 `MinerUConfig.io_path` 一致）。  
- 应用落盘到 **`/workspace/mineru-io/...`** 的路径，对应 MinerU 容器内为 **`/io/...`**，便于双方读写同一批文件。

### 7.5 启动顺序（与 MinerU 相关）

1. 启动 **`mineru-deploy`**（创建 **`mineru-stack`**）。  
2. 再启动或重启 **`app/app-deploy`**，使 `models-app` 加入该 external 网络。  
3. 若仅创建占位网络而未起 MinerU：`docker network create mineru-stack`（与 `app/app-deploy` 文档说明一致）。

---

## 8. 排障摘要

| 现象 | 处理方向 |
|------|----------|
| `invalid mount` / 路径不存在 | 在宿主机创建 **`MINERU_MODELS_HOST_PATH`**、**`MINERU_IO_HOST_PATH`**；Windows 注意路径格式与 Docker Desktop 共享盘 |
| 拉取镜像失败 | 检查 **`MINERU_IMAGE`**、内网 registry 登录、`docker pull` |
| 容器秒退 | 核对 **`command`**、查看 `logs`；对比官方镜像默认启动方式 |
| 仍访问公网下载 | 检查 **`HF_HUB_OFFLINE`**、**`TRANSFORMERS_OFFLINE`**、`models-dir` 是否完整 |
| OOM | 增大 **`MINERU_MEM_LIMIT`** 或改 GPU 模式；单任务并发保持为 1 |
| GPU 不可用 | 确认已叠加 **`docker-compose.gpu.yml`**、`nvidia-smi` 在宿主正常、驱动与 toolkit 版本匹配 |

---

## 9. 部署完整性自检（mineru-deploy 范围）

以下项全部满足，可视为 **本目录交付完整**（仍依赖运维填入真实镜像与模型）：

| 项 | 状态 |
|----|------|
| `docker-compose.yml` 可单独拉起 CPU 服务 | 已提供（`cpus`/`mem_limit` 对非 Swarm 生效） |
| `docker-compose.gpu.yml` 可叠加启用 GPU | 已提供 |
| `.env.example` 覆盖主要变量 | 已提供 |
| 离线模型路径与 `mineru-tools.json` 流程 | README §5 + 示例文件 |
| 首次部署步骤与运维命令 | README §2、§6 |
| 网络名与 `models-app` 调用方式、端口与 vLLM 区别 | README §7 |
| 排障与前置条件 | README §0、§8 |

**未在仓库内固定的事项（属正常）**：具体 **`MINERU_IMAGE` 标签**、**官方 `mineru-tools.json` 全量 schema**、**MinerU API 路径**（以镜像内 OpenAPI 为准）——须随你们选用的 **MinerU 版本** 从内网文档或制品同步。

---

更完整的总体架构与 RAG 接入路线图：`docs/MinerU-RAG-技术方案与实施清单.md`。
