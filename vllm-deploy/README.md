# vLLM 部署说明（Docker Compose）

本目录仅维护 **一种** 推荐部署方式：在 `docker/` 下使用 **Docker Compose** 构建镜像并运行服务。配置、启动脚本与模型通过 **卷挂载** 注入；镜像内仅包含基础环境、系统依赖与按构建参数选择的 Python 依赖。

## 适用场景

- **英伟达 GPU 服务器**：默认 `BASE_IMAGE` 为 `nvidia/cuda:12.3.0-runtime-ubuntu22.04`，`VLLM_REQUIREMENTS_PROFILE=full`，在镜像构建阶段通过 pip 安装 `vllm` 及配套依赖。
- **国产算力 / 厂商软件栈**：将 `BASE_IMAGE` 指向厂商提供的已适配镜像（已含 vLLM、PyTorch 等），并设置 `VLLM_REQUIREMENTS_PROFILE=extras`，仅安装 `pyyaml` / `requests` / `psutil`，**避免**用 PyPI 覆盖厂商预装推理栈。

## 前置条件

- 已安装 **Docker** 与 **Docker Compose**（`docker compose` 或 `docker-compose`） [Kylin系统中，使用命令一键部署：bash <(wget -qO- https://xuanyuan.cloud/docker.sh)  ]。
- **英伟达环境**：宿主机安装 **NVIDIA 驱动** 与 **NVIDIA Container Toolkit**。
- **国产环境**：按厂商文档安装对应驱动与容器运行时；本仓库通过平台化 compose overlay 管理硬件差异。

基础镜像需允许在构建阶段执行包安装与 `pip`（见 `docker/Dockerfile`）。当前仓库默认 `Dockerfile` 为 `yum/dnf` 版本；原 `apt-get` 版本已备份为 `docker/Dockerfile_bak`。若你的基础镜像是 Debian/Ubuntu 体系，请切回 `Dockerfile_bak` 或按需改造。

## 构建参数（`.env` 或 `docker compose build --build-arg`）

| 变量 | 含义 | 默认 |
|------|------|------|
| `BASE_IMAGE` | 构建阶段 `FROM` 的基础镜像 | `nvidia/cuda:12.3.0-runtime-ubuntu22.04` |
| `VLLM_REQUIREMENTS_PROFILE` | `full`：安装 `requirements.txt`（含 vLLM）；`extras`：仅 `requirements-extras.txt` | `full` |
| `VLLM_IMAGE` | 构建产出的镜像名 | `vllm-service:latest` |

在 `vllm-deploy/` 下复制并编辑环境变量：

```bash
cp .env.example .env
# 编辑 .env：国产场景示例
# BASE_IMAGE=your-registry/vendor-vllm:tag
# VLLM_REQUIREMENTS_PROFILE=extras
```

## 多平台 compose 结构（推荐）

为避免不同厂商显卡配置互相污染，仓库采用“基座 + 平台 overlay”：

- `docker/docker-compose.yml`：通用配置（镜像构建、端口、挂载、命令、健康检查）。
- `docker/docker-compose.nvidia.yml`：英伟达差异项（`CUDA_VISIBLE_DEVICES`、GPU reservation）。
- `docker/docker-compose.cambricon.yml`：寒武纪差异项（`privileged: true`、`MLU_VISIBLE_DEVICES`、`/dev` 透传）。
- `docker/docker-compose.mthreads.yml`：沐曦差异项（`privileged: true`、`MX_VISIBLE_DEVICES`、`/dev` 透传）。
- `docker/docker-compose.ascend.yml`：昇腾差异项（`privileged: true`、`ASCEND_RT_VISIBLE_DEVICES`、`/dev` 透传）。

平台选择方式：

- 脚本方式：`./deploy.sh --platform nvidia|cambricon|mthreads|ascend`
- 环境变量方式：在 `.env` 设置 `VLLM_PLATFORM=nvidia|cambricon|mthreads|ascend` 后直接 `./deploy.sh`

若你有其他国产平台（例如燧原等），建议继续新增对应 `docker-compose.<platform>.yml`，不要把所有平台逻辑塞在一个文件里。

## 参数来源与覆盖逻辑（模型启动 / 部署）

> 参数配置优先级顺序：.env、config/models.yaml、config/vllm.yaml 
> 为避免调参时改错文件，建议按下面理解：

1. **模型推理参数主来源：`config/models.yaml` + `config/vllm.yaml`**
   - `models.yaml`：模型预设（`path`、`dtype`、`max_model_len`、`tensor_parallel_size`、多模态参数等）。
   - `vllm.yaml`：服务默认参数（`server/model/hardware/performance/multimodal`）。
   - 启动时 `start.py` 会先读取 `vllm.yaml`，再按 `MODEL_PRESET` 应用 `models.yaml` 预设。

2. **环境变量会覆盖部分 YAML 参数（运行期）**
   - `start.py` 中 `_apply_env_overrides` 会用环境变量覆盖配置：
   - `VLLM_HOST`、`VLLM_PORT`、`MODEL_PATH`、`SERVED_MODEL_NAME`、
     `TENSOR_PARALLEL_SIZE`、`GPU_MEMORY_UTILIZATION`、`MAX_MODEL_LEN`、`MAX_NUM_SEQS`。
   - 因此同一参数若在 YAML 与 `.env` 同时配置，最终以环境变量为准。

3. **容器部署参数不在 YAML 中**
   - 端口映射、卷挂载、健康检查、日志策略、`privileged`、设备透传等在 `docker-compose*.yml`。
   - 平台切换（`nvidia/cambricon/mthreads/ascend`）由 `deploy.sh --platform` 或 `.env` 的 `VLLM_PLATFORM` 决定。

4. **镜像构建参数是 build 阶段，不属于运行期 vLLM 参数**
   - `BASE_IMAGE`、`VLLM_REQUIREMENTS_PROFILE` 在 `Dockerfile` + compose `build.args` 生效。
   - 修改这类参数后需要重新构建镜像（`up --build`）。

5. **推荐调参顺序（实践）**
   - 先在 `models.yaml` 维护各模型“标准预设”；
   - 用 `vllm.yaml` 放通用默认；
   - 仅把环境差异（机器端口、路径、卡可见性、少量临时覆盖）放到 `.env`。

## 模型权重准备

默认离线路径约定：宿主机 `MODEL_PATH=/opt/models/llm`，并通过 Compose 挂载到容器 `/workspace/models`。

```text
# 方式一：git-lfs
sudo yum install git-lfs   # 或 apt install git-lfs
git lfs install
mkdir -p /opt/models/llm
cd /opt/models/llm
git clone https://www.modelscope.cn/Qwen/Qwen2.5-VL-7B-Instruct.git
cd Qwen2.5-VL-7B-Instruct && git lfs pull
```

```text
# 方式二：ModelScope（国内常用）
pip install modelscope
python -c "from modelscope import snapshot_download; snapshot_download('qwen/Qwen2.5-VL-7B-Instruct', cache_dir='/opt/models/llm/Qwen2.5-VL-7B-Instruct')"
```

```text
# 方式三：Hugging Face（可配合镜像站）
pip install huggingface-hub
python -c "from huggingface_hub import snapshot_download; snapshot_download('Qwen/Qwen2.5-VL-7B-Instruct', local_dir='/opt/models/llm/Qwen2.5-VL-7B-Instruct', endpoint='https://hf-mirror.com')"
```

在 `config/vllm.yaml` / `config/models.yaml` 中确认模型路径与预设一致；若使用 `MODEL_PATH` 环境变量（默认 `/opt/models/llm`）挂载自定义目录，请与配置中的路径对应。

## 构建与启动（完整步骤）

`.env` 建议放在 **`vllm-deploy/` 根目录**（与 `.env.example` 同级）。在 `docker/` 下执行 Compose 时，需显式指定 `--env-file ../.env`，否则 `${BASE_IMAGE}`、`VLLM_REQUIREMENTS_PROFILE` 等构建变量不会从根目录 `.env` 读取。

1. **准备配置与环境变量**
   - 在 `vllm-deploy/` 下复制环境变量模板并按需修改：
     > 针对使用的显卡和部署的模型，关键配置项：
     > BASE_IMAGE -- 基础镜像(国产卡使用厂商提供pytorch+vllm的开发框架镜像)
     > VLLM_REQUIREMENTS_PROFILE -- 安装依赖的版本(区分国产卡和N卡，国产卡基础镜像中已安装适配版pytorch和vllm，不需要再次安装)
     > VLLM_PLATFORM -- 针对使用deploy.sh脚本进行部署时选择 对应显卡的 docker-compose配置文件
     > MODEL_PRESET -- 配置的部署模型(据此从config/models.yaml中读取配置)
     > *_VISIBLE_DEVICES -- 指定容器中可见的加速卡编号
     > TENSOR_PARALLEL_SIZE -- 模型并行张量的卡数

     ```bash
     cd vllm-deploy
     cp .env.example .env
     vi .env
     ```

2. **准备模型权重**
   - 按「模型权重准备」一节任意一种方式将模型下载到宿主机 `MODEL_PATH`（默认 `/opt/models/llm`）下，并在 `config/vllm.yaml` / `config/models.yaml` 中确认路径与预设一致。

3. **构建并启动服务**
   - 推荐使用一键脚本（自动带上 `--env-file ../.env` 与平台 overlay）：

     ```bash
     cd vllm-deploy
     chmod +x deploy.sh
     ./deploy.sh --platform nvidia
     ```

   - 或手动执行（在 `docker/` 下）：

     ```bash
     cd vllm-deploy/docker
     docker compose --env-file ../.env -f docker-compose.yml -f docker-compose.nvidia.yml up -d --build
     # 旧版：docker-compose --env-file ../.env -f docker-compose.yml -f docker-compose.nvidia.yml up -d --build
     ```

4. **（可选）使用 docker 目录下的 .env**
   - 若将 `.env` 放在 `docker/.env`，可直接：

     ```bash
     cd vllm-deploy/docker
     docker compose up -d --build
     ```

   - 此时不再读取上一级 `.env`。

- **启动命令**在 `docker-compose.yml` 的 `command` 中：`python3 /workspace/scripts/start.py start`（脚本来自仓库挂载，非镜像内 COPY）。
- **健康检查**在 `docker-compose.yml` 的 `healthcheck` 中：执行挂载的 `scripts/health.py`。

查看日志：

```bash
cd docker
docker compose logs -f
```

停止：

```bash
docker compose down
```

## 监控（可选）

```bash
cd docker
docker compose --profile monitoring up -d
```

Prometheus 监听 `9090`，配置见 `docker/prometheus.yml`。

## 部署后快速验证

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/models
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen2.5-vl-7b-instruct",
    "messages": [{"role": "user", "content": "你好，做个自我介绍"}],
    "max_tokens": 64,
    "temperature": 0.2
  }'
```

若 `served_model_name` 不是 `default`，请将请求中的 `model` 改为实际名称。

## 切换模型（改环境变量后重建容器）

在 `vllm-deploy/.env` 中修改 `MODEL_PRESET`、设备可见变量（英伟达用 `CUDA_VISIBLE_DEVICES`，寒武纪用 `MLU_VISIBLE_DEVICES`，其他国产用对应厂商变量）、`TENSOR_PARALLEL_SIZE` 等，然后：

```bash
cd vllm-deploy
./deploy.sh --platform nvidia
```

## LangChain 调用示例

```python
from langchain_openai import ChatOpenAI
import yaml
from pathlib import Path

config_path = Path("config/vllm.yaml")  # 宿主机上 vllm-deploy 目录
with open(config_path) as f:
    config = yaml.safe_load(f)

llm = ChatOpenAI(
    model=config["server"].get("served_model_name", "default"),
    openai_api_key="EMPTY",
    base_url=f"http://{config['server']['host']}:{config['server']['port']}/v1",
    temperature=0.7,
    max_tokens=512,
)
print(llm.invoke("你好，请介绍一下自己").content)
```

## 硬件与性能调优（摘录）

在 `config/vllm.yaml` 中按显存与并发调整，例如：

```yaml
# Qwen2.5-VL-7B（单卡 24GB 量级）
hardware:
  tensor_parallel_size: 1
  gpu_memory_utilization: 0.8

# 高并发
performance:
  max_num_seqs: 64
  max_num_batched_tokens: 65536
  enable_prefix_caching: true
```

更多预设见 `config/models.yaml`。
