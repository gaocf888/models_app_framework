# vLLM 部署说明（Docker Compose）

本目录仅维护 **一种** 推荐部署方式：在 `docker/` 下使用 **Docker Compose** 构建镜像并运行服务。配置、启动脚本与模型通过 **卷挂载** 注入；镜像内仅包含基础环境、系统依赖与按构建参数选择的 Python 依赖。

## 适用场景

- **英伟达 GPU 服务器**：默认 `BASE_IMAGE` 为 `nvidia/cuda:12.3.0-runtime-ubuntu22.04`，`VLLM_REQUIREMENTS_PROFILE=full`，在镜像构建阶段通过 pip 安装 `vllm` 及配套依赖。
- **国产算力 / 厂商软件栈**：将 `BASE_IMAGE` 指向厂商提供的已适配镜像（已含 vLLM、PyTorch 等），并设置 `VLLM_REQUIREMENTS_PROFILE=extras`，仅安装 `pyyaml` / `requests` / `psutil`，**避免**用 PyPI 覆盖厂商预装推理栈。

## 前置条件

- 已安装 **Docker** 与 **Docker Compose**（`docker compose` 或 `docker-compose`）。
- **英伟达环境**：宿主机安装 **NVIDIA 驱动** 与 **NVIDIA Container Toolkit**，以便 Compose 中 `deploy.resources.reservations.devices` 生效。
- **国产环境**：按厂商文档使用其容器运行时，并**按需修改** `docker/docker-compose.yml` 中与 GPU、设备、特权或 `deploy` 相关的段落（本仓库无法覆盖所有厂商差异）。

基础镜像需允许在构建阶段执行 `apt-get` 与 `pip`（见 Dockerfile）。若厂商镜像已预装 `python3`，重复安装通常无害或快速跳过，具体以镜像说明为准。

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

     ```bash
     cd vllm-deploy
     cp .env.example .env
     vi .env
     ```

2. **准备模型权重**
   - 按「模型权重准备」一节任意一种方式将模型下载到宿主机 `MODEL_PATH`（默认 `/opt/models/llm`）下，并在 `config/vllm.yaml` / `config/models.yaml` 中确认路径与预设一致。

3. **构建并启动服务**
   - 推荐使用一键脚本（自动带上 `--env-file ../.env`）：

     ```bash
     cd vllm-deploy
     chmod +x deploy.sh
     ./deploy.sh
     ```

   - 或手动执行（在 `docker/` 下）：

     ```bash
     cd vllm-deploy/docker
     docker compose --env-file ../.env up -d --build
     # 旧版：docker-compose --env-file ../.env up -d --build
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

在 `vllm-deploy/.env` 中修改 `MODEL_PRESET`、`CUDA_VISIBLE_DEVICES`、`TENSOR_PARALLEL_SIZE` 等，然后：

```bash
cd docker
docker compose up -d
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
