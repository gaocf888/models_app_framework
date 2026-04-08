# MinerU 部署说明（CPU/GPU 独立版）

本目录将 CPU 与 GPU 运行方式拆分为两套独立实现：

- CPU：`Dockerfile.cpu` + `docker-compose.cpu.yml`
- GPU：`Dockerfile.gpu` + `docker-compose.gpu.yml`

目标：
- 模型不进镜像，统一通过宿主机挂载卷提供
- 与 `models-app` 对接兼容（`http://mineru-api:8000`、共享 IO、`/io/mineru-output`）

## 1. 目录结构

- `Dockerfile.cpu`：CPU 镜像构建
- `Dockerfile.gpu`：GPU 镜像构建（CUDA 基础镜像）
- `docker-compose.cpu.yml`：CPU 编排（推荐入口）
- `docker-compose.gpu.yml`：GPU 编排（独立可运行）
- `docker-compose.yml`：CPU 兼容入口（与 `docker-compose.cpu.yml` 等效）
- `docker/entrypoint.sh`：容器入口（初始化 `/io/.hf_cache` 和 `/io/mineru-output`）
- `.env.example`：统一环境变量模板

## 2. 前提条件

- Docker / Docker Compose 可用
- 在线构建时可访问 PyPI（或配置 `PIP_INDEX_URL`）
- GPU 模式需：
  - NVIDIA 驱动
  - `nvidia-container-toolkit`
  - `docker run --rm --gpus all nvidia/cuda:12.3.0-base-ubuntu22.04 nvidia-smi` 可正常输出

> 下方3、4、5、6、7是完整的部署流程

## 3. 通用准备

```bash
cd mineru-deploy
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

必须修改 `.env`：
- `MINERU_MODELS_HOST_PATH`
- `MINERU_IO_HOST_PATH`

可选：
- `PIP_INDEX_URL`（国内镜像源）
- `MINERU_MODEL_SOURCE`（`huggingface` / `modelscope` / `local`）

## 4. 在线下载模型 vs 离线模型（关键）

当前实现**同时支持在线与离线**，由 `.env` 决定：

### 4.1 在线模式（默认）

```env
MINERU_MODEL_SOURCE=modelscope
HF_HUB_OFFLINE=0
TRANSFORMERS_OFFLINE=0
```

说明：
- 运行时允许在线拉取模型
- 模型缓存写入 `/io/.hf_cache`（宿主机对应 `${MINERU_IO_HOST_PATH}/.hf_cachecd `）
- 适合可联网环境

> 国内网络可选：`MINERU_MODEL_SOURCE=modelscope`

### 4.2 离线模式（提前下载）

```env
MINERU_MODEL_SOURCE=local
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

说明：
- 运行时不再联网拉取模型
- 需要你提前把模型文件放入 `${MINERU_MODELS_HOST_PATH}`（容器内 `/models`）
  > 下载方法：
  - 在魔塔社区中搜索 OpenDataLab/PDF-Extract-Kit-1.0
  - 使用git lfs下载到 ${MINERU_MODELS_HOST_PATH} 路径下
  > 为保证下载后路径一致，建议先在有网环境部署，然后使用docker cp从容器中复制下载后的模型到本地，然后拷贝到离线服务器的${MINERU_MODELS_HOST_PATH}路径中
    （docker cp mineru-api:/root/.cache/modelscope/hub/models/OpenDataLab /data/mineru/models/OpenDataLab）
       下载后路径要确保下面的路径：
         宿主：${MINERU_MODELS_HOST_PATH}/OpenDataLab/PDF-Extract-Kit-1.0/...
         容器：/models/OpenDataLab/PDF-Extract-Kit-1.0/...
- 适合离线/内网环境

## 5. CPU 模式（独立实现）

### 5.1 构建并启动（推荐）

```bash
docker compose --env-file .env -f docker-compose.cpu.yml build
docker compose --env-file .env -f docker-compose.cpu.yml up -d
docker compose --env-file .env -f docker-compose.cpu.yml logs -f mineru-api
```

### 5.2 兼容入口（可选）

`docker-compose.yml` 与 `docker-compose.cpu.yml` 等效，也可使用：

```bash
docker compose --env-file .env build
docker compose --env-file .env up -d
```

### 5.3 验证

- `http://<host>:<MINERU_PORT>/docs`
- `http://<host>:<MINERU_PORT>/health`

curl -l http://127.0.0.1:8009/health

## 6. GPU 模式（独立实现）

### 6.1 关键说明

- `docker-compose.gpu.yml` 为独立编排，不依赖 CPU compose
- GPU 镜像使用 `Dockerfile.gpu` 构建
- 默认安装 CUDA 版 PyTorch（可通过 `.env` 开关控制）

### 6.2 关键 `.env` 配置

```env
MINERU_NVIDIA_VISIBLE_DEVICES=0
INSTALL_CUDA_TORCH=1
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121
```

### 6.3 构建并启动

```bash
docker compose --env-file .env -f docker-compose.gpu.yml build
docker compose --env-file .env -f docker-compose.gpu.yml up -d
docker compose --env-file .env -f docker-compose.gpu.yml logs -f mineru-api
```

## 7. 离线服务器部署

### 7.1 CPU 离线
> 仅针对 **CPU 版本**，GPU 版本请忽略本小节（可按需参考 7.2）。

推荐的最简单离线方案：在一台 **有外网的服务器** 上构建好镜像并导出，然后在 **无外网的目标服务器** 上导入并直接运行。

**步骤 1：有网机器上构建并导出镜像**

1. 复制或进入 `mineru-deploy/` 目录：

   ```bash
   cd mineru-deploy
   ```

2. 使用 `Dockerfile.cpu` 构建 CPU 镜像（示例镜像名 `mineru-cpu:py311`，可自行调整）：

   ```bash
   docker build -f Dockerfile.cpu -t mineru-cpu:py311 .
   ```

3. 导出镜像为离线文件：

   ```bash
   docker save -o mineru-cpu-py311.tar mineru-cpu:py311
   ```

4. 将 `mineru-cpu-py311.tar` 拷贝到离线服务器（U 盘 / 内网文件服务器等）。

**步骤 2：离线机器上导入镜像并启动**

1. 在离线服务器导入镜像：

   ```bash
   docker load -i mineru-cpu-py311.tar
   ```

2. 在离线服务器上准备配置与挂载目录：

   ```bash
   cd mineru-deploy
   cp .env.example .env
   mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
   ```

3. 在 `.env` 中设置 CPU 镜像名（与上面构建时一致）：

   ```env
   MINERU_CPU_IMAGE=mineru-cpu:py311
   ```

4. 启动 CPU 版本（离线无需再构建，只需 `up`）：

   ```bash
   docker compose --env-file .env -f docker-compose.cpu.yml up -d
   docker compose --env-file .env -f docker-compose.cpu.yml logs -f mineru-api
   ```

### 7.2 GPU 离线

有网机器：

```bash
docker compose --env-file .env -f docker-compose.gpu.yml build
docker save -o mineru-gpu.tar ${MINERU_GPU_IMAGE}
```

离线机器：

```bash
docker load -i mineru-gpu.tar
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

## 8. 与 models-app 对接约定

- `models-app` 调用地址：`http://mineru-api:8000`
- Docker 网络名：`MINERU_NETWORK_NAME`（默认 `mineru-stack`）
- `app/app-deploy` 需加入同名 external 网络
- 两边共享同一宿主机 IO 路径
- 输出根固定：`MINERU_API_OUTPUT_ROOT=/io/mineru-output`

## 9. 环境变量速查（.env）

### 构建相关

- `MINERU_CPU_IMAGE`：CPU 镜像名
- `MINERU_GPU_IMAGE`：GPU 镜像名
- `MINERU_CPU_PIP_SPEC`：CPU pip 安装规格（如 `mineru[core]`）
- `MINERU_GPU_PIP_SPEC`：GPU pip 安装规格
- `PIP_INDEX_URL`：pip 镜像源
- `INSTALL_CUDA_TORCH`：GPU 镜像是否预装 CUDA 版 torch（`1/0`）
- `TORCH_INDEX_URL`：CUDA 版 torch 索引地址

### 运行相关

- `MINERU_CONTAINER_NAME`
- `MINERU_PORT`（宿主映射端口）
- `MINERU_NVIDIA_VISIBLE_DEVICES`（GPU 设备）
- `MINERU_MODELS_HOST_PATH`（挂载到 `/models`）
- `MINERU_IO_HOST_PATH`（挂载到 `/io`）
- `MINERU_MODEL_SOURCE`（`huggingface` / `modelscope` / `local`）
- `HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`
- `MINERU_CPU_LIMIT`、`MINERU_MEM_LIMIT`
- `OMP_NUM_THREADS`、`MKL_NUM_THREADS`、`OPENBLAS_NUM_THREADS`
- `NUMEXPR_NUM_THREADS`、`TORCH_NUM_THREADS`、`TORCH_NUM_INTEROP_THREADS`
- `MINERU_NETWORK_NAME`

## 10. 生产环境 CPU 防卡死配置（推荐）

为避免大 PDF（如 300 页）导致 CPU 突刺或整机卡顿，建议同时控制：
- 容器 CPU 配额：`MINERU_CPU_LIMIT`
- 线程数硬限制：`OMP/MKL/OPENBLAS/NUMEXPR/TORCH_*`
- 应用侧单任务负载：`MINERU_PAGE_BATCH_SIZE`、`MINERU_FORMULA_ENABLE`、`MINERU_TABLE_ENABLE`
- 全局并发：`MINERU_MAX_CONCURRENT`（在 `app/app-deploy/.env`）

### 10.1 档位 A（保守稳定）

目标：CPU 约 30%~50%，优先稳定。

`mineru-deploy/.env`
```env
MINERU_CPU_LIMIT=4.0
MINERU_MEM_LIMIT=16g
OMP_NUM_THREADS=6
MKL_NUM_THREADS=6
OPENBLAS_NUM_THREADS=6
NUMEXPR_NUM_THREADS=6
TORCH_NUM_THREADS=6
TORCH_NUM_INTEROP_THREADS=2
```

`app/app-deploy/.env`
```env
MINERU_MAX_CONCURRENT=1
MINERU_PAGE_BATCH_SIZE=40
MINERU_FORMULA_ENABLE=false
MINERU_TABLE_ENABLE=true
MINERU_TIMEOUT_S=2400
```

### 10.2 档位 B（平衡吞吐，默认推荐）

目标：CPU 约 50%~70%，质量与稳定性平衡。

`mineru-deploy/.env`
```env
MINERU_CPU_LIMIT=6.0
MINERU_MEM_LIMIT=20g
OMP_NUM_THREADS=8
MKL_NUM_THREADS=8
OPENBLAS_NUM_THREADS=8
NUMEXPR_NUM_THREADS=8
TORCH_NUM_THREADS=8
TORCH_NUM_INTEROP_THREADS=2
```

`app/app-deploy/.env`
```env
MINERU_MAX_CONCURRENT=1
MINERU_PAGE_BATCH_SIZE=50
MINERU_FORMULA_ENABLE=true
MINERU_TABLE_ENABLE=true
MINERU_TIMEOUT_S=2400
```

### 10.3 档位 C（高吞吐，谨慎）

目标：CPU 约 70%~85%，吞吐优先，需监控。

`mineru-deploy/.env`
```env
MINERU_CPU_LIMIT=8.0
MINERU_MEM_LIMIT=24g
OMP_NUM_THREADS=10
MKL_NUM_THREADS=10
OPENBLAS_NUM_THREADS=10
NUMEXPR_NUM_THREADS=10
TORCH_NUM_THREADS=10
TORCH_NUM_INTEROP_THREADS=2
```

`app/app-deploy/.env`
```env
MINERU_MAX_CONCURRENT=1
MINERU_PAGE_BATCH_SIZE=60
MINERU_FORMULA_ENABLE=true
MINERU_TABLE_ENABLE=true
MINERU_TIMEOUT_S=3000
```

### 10.4 调参顺序（建议）

出现 CPU 过高时，按以下顺序逐步收敛：
1. 先把 `MINERU_PAGE_BATCH_SIZE` 调小（60 → 50 → 40）
2. 再关闭 `MINERU_FORMULA_ENABLE`
3. 再把线程数从 8 降到 6
4. 最后再降低 `MINERU_CPU_LIMIT`

# MinerU 部署说明（CPU/GPU 独立版）

本目录将 CPU 与 GPU 运行方式拆分为两套独立实现：

- CPU：`Dockerfile.cpu` + `docker-compose.cpu.yml`
- GPU：`Dockerfile.gpu` + `docker-compose.gpu.yml`

目标：
- 模型不进镜像，统一通过宿主机挂载卷提供
- 与 `models-app` 对接兼容（`http://mineru-api:8000`、共享 IO、`/io/mineru-output`）

## 1. 目录结构

- `Dockerfile.cpu`：CPU 镜像构建
- `Dockerfile.gpu`：GPU 镜像构建（CUDA 基础镜像）
- `docker-compose.cpu.yml`：CPU 编排（推荐入口）
- `docker-compose.gpu.yml`：GPU 编排（独立可运行）
- `docker-compose.yml`：CPU 兼容入口（与 `docker-compose.cpu.yml` 等效）
- `docker/entrypoint.sh`：容器入口（初始化 `/io/.hf_cache` 和 `/io/mineru-output`）
- `.env.example`：统一环境变量模板

## 2. 前提条件

- Docker / Docker Compose 可用
- 在线构建时可访问 PyPI（或配置 `PIP_INDEX_URL`）
- GPU 模式需：
  - NVIDIA 驱动
  - `nvidia-container-toolkit`
  - `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` 可正常输出

## 3. 通用准备

```bash
cd mineru-deploy
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

必须修改 `.env`：
- `MINERU_MODELS_HOST_PATH`
- `MINERU_IO_HOST_PATH`

可选：
- `PIP_INDEX_URL`（国内镜像源）
- `MINERU_MODEL_SOURCE`（`huggingface` / `modelscope` / `local`）

## 4. CPU 模式（独立实现）

### 4.1 构建并启动（推荐）

```bash
docker compose --env-file .env -f docker-compose.cpu.yml build
docker compose --env-file .env -f docker-compose.cpu.yml up -d
docker compose --env-file .env -f docker-compose.cpu.yml logs -f mineru-api
```

### 4.2 兼容入口（可选）

`docker-compose.yml` 与 `docker-compose.cpu.yml` 等效，也可使用：

```bash
docker compose --env-file .env build
docker compose --env-file .env up -d
```

### 4.3 验证

- `http://<host>:<MINERU_PORT>/docs`
- `http://<host>:<MINERU_PORT>/health`

## 5. GPU 模式（独立实现）

### 5.1 关键说明

- `docker-compose.gpu.yml` 为独立编排，不依赖 CPU compose
- GPU 镜像使用 `Dockerfile.gpu` 构建
- 默认安装 CUDA 版 PyTorch（可通过 `.env` 开关控制）

### 5.2 关键 `.env` 配置

```env
MINERU_NVIDIA_VISIBLE_DEVICES=0
INSTALL_CUDA_TORCH=1
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121
```

### 5.3 构建并启动

```bash
docker compose --env-file .env -f docker-compose.gpu.yml build
docker compose --env-file .env -f docker-compose.gpu.yml up -d
docker compose --env-file .env -f docker-compose.gpu.yml logs -f mineru-api
```

## 6. 离线服务器部署

### 6.1 CPU 离线

有网机器：

```bash
docker compose --env-file .env -f docker-compose.cpu.yml build
docker save -o mineru-cpu.tar ${MINERU_CPU_IMAGE}
```

离线机器：

```bash
docker load -i mineru-cpu.tar
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

### 6.2 GPU 离线

有网机器：

```bash
docker compose --env-file .env -f docker-compose.gpu.yml build
docker save -o mineru-gpu.tar ${MINERU_GPU_IMAGE}
```

离线机器：

```bash
docker load -i mineru-gpu.tar
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

### 6.3 离线运行开关（CPU/GPU 通用）

```env
MINERU_MODEL_SOURCE=local
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

## 7. 与 models-app 对接约定

- `models-app` 调用地址：`http://mineru-api:8000`
- Docker 网络名：`MINERU_NETWORK_NAME`（默认 `mineru-stack`）
- `app/app-deploy` 需加入同名 external 网络
- 两边共享同一宿主机 IO 路径
- 输出根固定：`MINERU_API_OUTPUT_ROOT=/io/mineru-output`

## 8. 环境变量速查（.env）

### 构建相关

- `MINERU_CPU_IMAGE`：CPU 镜像名
- `MINERU_GPU_IMAGE`：GPU 镜像名
- `MINERU_CPU_PIP_SPEC`：CPU pip 安装规格（如 `mineru[core]`）
- `MINERU_GPU_PIP_SPEC`：GPU pip 安装规格
- `PIP_INDEX_URL`：pip 镜像源
- `INSTALL_CUDA_TORCH`：GPU 镜像是否预装 CUDA 版 torch（`1/0`）
- `TORCH_INDEX_URL`：CUDA 版 torch 索引地址

### 运行相关

- `MINERU_CONTAINER_NAME`
- `MINERU_PORT`（宿主映射端口）
- `MINERU_NVIDIA_VISIBLE_DEVICES`（GPU 设备）
- `MINERU_MODELS_HOST_PATH`（挂载到 `/models`）
- `MINERU_IO_HOST_PATH`（挂载到 `/io`）
- `MINERU_MODEL_SOURCE`（`huggingface` / `modelscope` / `local`）
- `HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`
- `MINERU_CPU_LIMIT`、`MINERU_MEM_LIMIT`
- `MINERU_NETWORK_NAME`

# MinerU 部署说明（CPU/GPU 独立版）

本目录将 CPU 与 GPU 运行方式拆分为两套独立实现：

- CPU：`Dockerfile.cpu` + `docker-compose.cpu.yml`
- GPU：`Dockerfile.gpu` + `docker-compose.gpu.yml`

目标：
- 模型不进镜像，统一通过宿主机挂载卷提供
- 与 `models-app` 对接兼容（`http://mineru-api:8000`、共享 IO、`/io/mineru-output`）

---

## 1. 目录结构

- `Dockerfile.cpu`：CPU 镜像构建
- `Dockerfile.gpu`：GPU 镜像构建（CUDA 基础镜像）
- `docker-compose.cpu.yml`：CPU 编排（推荐入口）
- `docker-compose.gpu.yml`：GPU 编排（独立可运行）
- `docker-compose.yml`：CPU 兼容入口（与 `docker-compose.cpu.yml` 等效）
- `docker/entrypoint.sh`：容器入口（初始化 `/io/.hf_cache` 和 `/io/mineru-output`）
- `.env.example`：统一环境变量模板

---

## 2. 前提条件

- Docker / Docker Compose 可用
- 在线构建时可访问 PyPI（或配置 `PIP_INDEX_URL`）
- GPU 模式需：
  - NVIDIA 驱动
  - `nvidia-container-toolkit`
  - `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` 可正常输出

---

## 3. 通用准备

```bash
cd mineru-deploy
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

必须修改 `.env`：
- `MINERU_MODELS_HOST_PATH`
- `MINERU_IO_HOST_PATH`

可选：
- `PIP_INDEX_URL`（国内镜像源）
- `MINERU_MODEL_SOURCE`（`huggingface` / `modelscope` / `local`）

---

## 4. CPU 模式（独立实现）

### 4.1 构建并启动（推荐）

```bash
docker compose --env-file .env -f docker-compose.cpu.yml build
docker compose --env-file .env -f docker-compose.cpu.yml up -d
docker compose --env-file .env -f docker-compose.cpu.yml logs -f mineru-api
```

### 4.2 兼容入口（可选）

`docker-compose.yml` 与 `docker-compose.cpu.yml` 等效，也可使用：

```bash
docker compose --env-file .env build
docker compose --env-file .env up -d
```

### 4.3 验证

- `http://<host>:<MINERU_PORT>/docs`
- `http://<host>:<MINERU_PORT>/health`

---

## 5. GPU 模式（独立实现）

### 5.1 关键说明

- `docker-compose.gpu.yml` 为独立编排，不依赖 CPU compose
- GPU 镜像使用 `Dockerfile.gpu` 构建
- 默认安装 CUDA 版 PyTorch（可通过 `.env` 开关控制）

### 5.2 关键 `.env` 配置

```env
MINERU_NVIDIA_VISIBLE_DEVICES=0
INSTALL_CUDA_TORCH=1
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121
```

### 5.3 构建并启动

```bash
docker compose --env-file .env -f docker-compose.gpu.yml build
docker compose --env-file .env -f docker-compose.gpu.yml up -d
docker compose --env-file .env -f docker-compose.gpu.yml logs -f mineru-api
```

---

## 6. 离线服务器部署

### 6.1 CPU 离线

有网机器：

```bash
docker compose --env-file .env -f docker-compose.cpu.yml build
docker save -o mineru-cpu.tar ${MINERU_CPU_IMAGE}
```

离线机器：

```bash
docker load -i mineru-cpu.tar
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

### 6.2 GPU 离线

有网机器：

```bash
docker compose --env-file .env -f docker-compose.gpu.yml build
docker save -o mineru-gpu.tar ${MINERU_GPU_IMAGE}
```

离线机器：

```bash
docker load -i mineru-gpu.tar
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

### 6.3 离线运行开关（CPU/GPU 通用）

```env
MINERU_MODEL_SOURCE=local
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

---

## 7. 与 models-app 对接约定

- `models-app` 调用地址：`http://mineru-api:8000`
- Docker 网络名：`MINERU_NETWORK_NAME`（默认 `mineru-stack`）
- `app/app-deploy` 需加入同名 external 网络
- 两边共享同一宿主机 IO 路径
- 输出根固定：`MINERU_API_OUTPUT_ROOT=/io/mineru-output`

---

## 8. 环境变量速查（.env）

### 构建相关

- `MINERU_CPU_IMAGE`：CPU 镜像名
- `MINERU_GPU_IMAGE`：GPU 镜像名
- `MINERU_CPU_PIP_SPEC`：CPU pip 安装规格（如 `mineru[core]`）
- `MINERU_GPU_PIP_SPEC`：GPU pip 安装规格
- `PIP_INDEX_URL`：pip 镜像源
- `INSTALL_CUDA_TORCH`：GPU 镜像是否预装 CUDA 版 torch（`1/0`）
- `TORCH_INDEX_URL`：CUDA 版 torch 索引地址

### 运行相关

- `MINERU_CONTAINER_NAME`
- `MINERU_PORT`（宿主映射端口）
- `MINERU_NVIDIA_VISIBLE_DEVICES`（GPU 设备）
- `MINERU_MODELS_HOST_PATH`（挂载到 `/models`）
- `MINERU_IO_HOST_PATH`（挂载到 `/io`）
- `MINERU_MODEL_SOURCE`（`huggingface` / `modelscope` / `local`）
- `HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`
- `MINERU_CPU_LIMIT`、`MINERU_MEM_LIMIT`
- `MINERU_NETWORK_NAME`

# MinerU 部署说明（CPU/GPU 独立版）

本目录将 CPU 与 GPU 运行方式彻底拆分，分别使用独立 Dockerfile 和 Compose：

- CPU：`Dockerfile.cpu` + `docker-compose.cpu.yml`（默认推荐）
- GPU：`Dockerfile.gpu` + `docker-compose.gpu.yml`

目标：
- 模型不进镜像，统一通过宿主机挂载卷提供
- 保持与 `models-app` 对接兼容（`http://mineru-api:8000`、共享 IO、`/io/mineru-output`）

---

## 1. 目录结构

- `Dockerfile.cpu`：CPU 镜像构建
- `Dockerfile.gpu`：GPU 镜像构建（CUDA 基础镜像）
- `docker-compose.cpu.yml`：CPU 编排（推荐主入口）
- `docker-compose.gpu.yml`：GPU 编排（独立可运行）
- `docker-compose.yml`：CPU 兼容入口（与 `docker-compose.cpu.yml` 等效）
- `docker/entrypoint.sh`：容器启动入口（初始化 `/io/.hf_cache` 和 `/io/mineru-output`）
- `.env.example`：统一环境变量模板

---

## 2. 前提条件

- Docker / Docker Compose 可用
- 在线构建时可访问 PyPI（或配置 `PIP_INDEX_URL`）
- GPU 模式需：
  - NVIDIA 驱动
  - `nvidia-container-toolkit`
  - `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` 能正常输出

---

## 3. 通用准备

```bash
cd mineru-deploy
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

必须修改 `.env`：
- `MINERU_MODELS_HOST_PATH`
- `MINERU_IO_HOST_PATH`

可选：
- `PIP_INDEX_URL`（国内镜像源）
- `MINERU_MODEL_SOURCE`（`huggingface` / `modelscope` / `local`）

---

## 4. CPU 模式（独立实现）

### 4.1 构建并启动（推荐）

```bash
docker compose --env-file .env -f docker-compose.cpu.yml build
docker compose --env-file .env -f docker-compose.cpu.yml up -d
docker compose --env-file .env -f docker-compose.cpu.yml logs -f mineru-api
```

### 4.2 兼容入口（可选）

`docker-compose.yml` 与 `docker-compose.cpu.yml` 等效，也可用：

```bash
docker compose --env-file .env build
docker compose --env-file .env up -d
```

### 4.3 验证

- `http://<host>:<MINERU_PORT>/docs`
- `http://<host>:<MINERU_PORT>/health`

---

## 5. GPU 模式（独立实现）

### 5.1 关键说明

- `docker-compose.gpu.yml` 是独立编排，不依赖 CPU compose
- GPU 镜像使用 `Dockerfile.gpu` 构建
- 默认会安装 CUDA 版 PyTorch（可用 `.env` 开关控制）

### 5.2 关键 `.env` 配置

```env
MINERU_NVIDIA_VISIBLE_DEVICES=0
INSTALL_CUDA_TORCH=1
TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121
```

### 5.3 构建并启动

```bash
docker compose --env-file .env -f docker-compose.gpu.yml build
docker compose --env-file .env -f docker-compose.gpu.yml up -d
docker compose --env-file .env -f docker-compose.gpu.yml logs -f mineru-api
```

---

## 6. 离线服务器部署

### 6.1 CPU 离线

有网机器：

```bash
docker compose --env-file .env -f docker-compose.cpu.yml build
docker save -o mineru-cpu.tar ${MINERU_CPU_IMAGE}
```

离线机器：

```bash
docker load -i mineru-cpu.tar
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

### 6.2 GPU 离线

有网机器：

```bash
docker compose --env-file .env -f docker-compose.gpu.yml build
docker save -o mineru-gpu.tar ${MINERU_GPU_IMAGE}
```

离线机器：

```bash
docker load -i mineru-gpu.tar
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

### 6.3 离线运行开关（CPU/GPU 通用）

```env
MINERU_MODEL_SOURCE=local
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

---

## 7. 与 models-app 对接约定

- `models-app` 调用地址保持：`http://mineru-api:8000`
- Docker 网络名：`MINERU_NETWORK_NAME`（默认 `mineru-stack`）
- `app/app-deploy` 需加入同名 external 网络
- 两边共享同一宿主机 IO 路径
- 输出根固定：`MINERU_API_OUTPUT_ROOT=/io/mineru-output`

---

## 8. 环境变量速查（.env）

### 构建相关

- `MINERU_CPU_IMAGE`：CPU 镜像名
- `MINERU_GPU_IMAGE`：GPU 镜像名
- `MINERU_CPU_PIP_SPEC`：CPU 镜像 pip 安装规格（如 `mineru[core]`）
- `MINERU_GPU_PIP_SPEC`：GPU 镜像 pip 安装规格
- `PIP_INDEX_URL`：pip 镜像源
- `INSTALL_CUDA_TORCH`：GPU 镜像是否预装 CUDA 版 torch（`1/0`）
- `TORCH_INDEX_URL`：CUDA 版 torch 索引地址

### 运行相关

- `MINERU_CONTAINER_NAME`
- `MINERU_PORT`（宿主映射端口）
- `MINERU_NVIDIA_VISIBLE_DEVICES`（GPU 设备）
- `MINERU_MODELS_HOST_PATH`（挂载到 `/models`）
- `MINERU_IO_HOST_PATH`（挂载到 `/io`）
- `MINERU_MODEL_SOURCE`（`huggingface` / `modelscope` / `local`）
- `HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`
- `MINERU_CPU_LIMIT`、`MINERU_MEM_LIMIT`
- `MINERU_NETWORK_NAME`

# MinerU 部署说明（源码构建版）

本目录用于独立部署 MinerU 服务，采用以下方式：

- 在 Docker 镜像构建阶段通过 Python/pip 安装 MinerU
- 模型不进镜像，全部通过宿主机卷挂载
- 默认 CPU，GPU 通过 `docker-compose.gpu.yml` 叠加
- 与 `models-app` 保持兼容：`http://mineru-api:8000`、共享 IO、`/io/mineru-output`

> 当前版本不再依赖 `config/mineru-tools.json`。

## 一、目录结构

- `Dockerfile`：构建镜像并安装 MinerU
- `docker/entrypoint.sh`：容器入口（初始化 `/io` 相关目录）
- `docker-compose.yml`：默认 CPU 编排
- `docker-compose.gpu.yml`：GPU 叠加编排
- `.env.example`：环境变量模板

## 二、关键设计说明

1. **不再依赖 `config/mineru-tools.json`**
   - `docker-compose.yml` 已移除 `MINERU_TOOLS_CONFIG_JSON` 与 config 文件挂载
   - 启动脚本不再检查该文件

2. **模型不进镜像，全部卷挂载**
   - `${MINERU_MODELS_HOST_PATH}:/models:ro`
   - `${MINERU_IO_HOST_PATH}:/io`（缓存与输出目录）

3. **保持与 models-app 对接兼容**
   - 服务地址：`http://mineru-api:8000`
   - 输出根：`/io/mineru-output`（`MINERU_API_OUTPUT_ROOT`）
   - 网络：`MINERU_NETWORK_NAME`（默认 `mineru-stack`）

## 三、前提条件

- Docker / Docker Compose 可用
- 在线构建时可访问 PyPI（或配置镜像源）
- GPU 模式需安装 `nvidia-container-toolkit`

## 四、在线部署（默认 CPU）

```bash
cd mineru-deploy
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

编辑 `.env` 至少这两项：

- `MINERU_MODELS_HOST_PATH`
- `MINERU_IO_HOST_PATH`

可选固定 MinerU 版本（推荐生产固定）：

```env
MINERU_PIP_SPEC=mineru[core]==<version>
```

构建并启动：

```bash
docker compose --env-file .env build
docker compose --env-file .env up -d
docker compose --env-file .env logs -f mineru-api
```

验证：

- `http://<host>:<MINERU_PORT>/docs`
- `http://<host>:<MINERU_PORT>/health`

## 五、GPU 启动（可选）

在 `.env` 中设置：

```env
MINERU_DEVICE_MODE=gpu
```

然后叠加 GPU compose：

```bash
docker compose --env-file .env -f docker-compose.yml -f docker-compose.gpu.yml up -d
```

## 六、离线服务器部署

### 1) 在有网机器构建并导出镜像

```bash
docker compose --env-file .env build
docker save -o mineru-local.tar ${MINERU_LOCAL_IMAGE}
```

### 2) 在离线机器导入并启动

```bash
docker load -i mineru-local.tar
cp .env.example .env
mkdir -p <MINERU_MODELS_HOST_PATH> <MINERU_IO_HOST_PATH>
```

离线运行必须设置：

```env
MINERU_MODEL_SOURCE=local
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

启动：

```bash
docker compose --env-file .env up -d
```

## 七、环境变量说明（`.env`）

### 构建相关

- `MINERU_LOCAL_IMAGE`：本地构建后的镜像名
- `MINERU_PIP_SPEC`：pip 安装规格（如 `mineru[core]`）
- `PIP_INDEX_URL`：可选 pip 镜像源

### 运行相关

- `MINERU_CONTAINER_NAME`：容器名
- `MINERU_PORT`：宿主机映射端口（默认 `8009`）
- `MINERU_DEVICE_MODE`：`cpu` / `gpu`
- `MINERU_NVIDIA_VISIBLE_DEVICES`：GPU 设备选择（GPU 模式）
- `MINERU_MODELS_HOST_PATH`：宿主模型目录（挂载到 `/models`）
- `MINERU_IO_HOST_PATH`：宿主 IO 目录（挂载到 `/io`）
- `MINERU_MODEL_SOURCE`：`huggingface` / `modelscope` / `local`
- `HF_HUB_OFFLINE`、`TRANSFORMERS_OFFLINE`：离线开关
- `MINERU_CPU_LIMIT`、`MINERU_MEM_LIMIT`：资源限制
- `MINERU_NETWORK_NAME`：Docker 网络名（默认 `mineru-stack`）

## 八、与 models-app 对接约定

- models-app 调用地址：`http://mineru-api:8000`
- `app/app-deploy` 需加入同名 external 网络（默认 `mineru-stack`）
- 两边共享同一宿主机 IO 路径（保证输入/输出一致）
- `MINERU_API_OUTPUT_ROOT=/io/mineru-output` 已在 compose 中固定配置

## 九、常用命令

```bash
# 构建
docker compose --env-file .env build

# 启动
docker compose --env-file .env up -d

# 查看日志
docker compose --env-file .env logs -f mineru-api

# 停止并删除容器
docker compose --env-file .env down
```