# 华为 Atlas 300I Duo_96G 基础环境及应用部署方案

> **文档性质**：面向 `dev_djs`（地面沉降）项目在 **华为 Atlas 300I Duo_96G + 双路 32 核 CPU（ARM/C86 适配）+ 银河麒麟 Kylin V10 SP3** 上的宿主机基础环境与默认必部应用部署方案。  
> **配套清单**：勾选式进度见 [`工作清单-华为Atlas300IDuo.md`](./工作清单-华为Atlas300IDuo.md)。  
> **对齐仓库**：`vllm-deploy/`、`rag_db-deploy/`、`mineru-deploy/`、`app/app-deploy/`；通用运维见 `enterprise-level_transformation_docs/项目整体部署运维手册.md`。  
> **版本**：2026-07（目标态方案；昇腾侧部分 compose 仍待合入，文中以「现状 / 目标」标注）。

---

## 1. 目标与范围

### 1.1 目标

在单台（或同构）Atlas 300I Duo_96G 推理服务器上，完成：

1. **宿主机基础环境**：NPU 驱动/固件、Docker、Ascend 容器运行时、目录与内核参数；
2. **默认必部应用栈**：EasySearch → vLLM（昇腾）→ MinerU（昇腾）→ models-app（昇腾嵌入/重排）+ Redis + MinIO；
3. **可验收、可回滚** 的配置约定与资源切分。

### 1.2 现场约束（已确认）

| 项 | 值 |
|----|-----|
| 加速卡 | 华为 **Atlas 300I Duo_96G**（双芯推理卡，合计约 96GB 显存） |
| CPU | **双路**，单颗 **32 核**（合计 **64 逻辑核量级**，以 `lscpu` 为准），主频 **≥2.0GHz** |
| CPU 架构要求 | 标书/交付要求 **支持 ARM / C86 架构适配**；现场必须落成其一并全程同架构选型（见 §1.2.1） |
| 操作系统 | **银河麒麟 Kylin V10 SP3**（安装介质须与 CPU 架构匹配） |
| 项目分支 | `dev_djs` |
| 设备可见性变量 | `ASCEND_RT_VISIBLE_DEVICES`（昇腾；勿与 CUDA/沐曦变量混用作为唯一依据） |

#### 1.2.1 ARM 与 C86：必须先锁定落地架构

「支持 ARM/C86 适配」表示方案与制品要能覆盖两条信创 CPU 路线，**不等于**一台机器同时跑两种指令集。上线前用现场机器确认唯一落地值：

| 落地路线 | 典型 CPU | `uname -m` | Docker / 昇腾镜像架构 | 说明 |
|----------|----------|------------|------------------------|------|
| **C86** | 海光（Hygon）等 | **`x86_64`** | **`linux/amd64`** | 指令集兼容 x86_64；驱动 run 包选 `x86_64`/`amd64` 变体 |
| **ARM** | 鲲鹏 / 飞腾等 | **`aarch64`** | **`linux/arm64`** | 驱动、CANN、vLLM/torch_npu 镜像均须 **arm64**；与 amd64 制品 **不互通** |

硬性规则：

1. **NPU 驱动 `.run`、CANN、基础镜像、业务镜像四者架构必须与 `uname -m` 一致**；混用会出现 `exec format error` 或驱动无法加载。  
2. 麒麟 V10 SP3 亦有按架构区分的安装包/内核；换 CPU 路线等于换一整套 OS+驱动+镜像矩阵，而不是只改一个 compose 文件。  
3. 仓库内沐曦示例镜像多为 `…-amd64`，**不可**在 ARM 昇腾机上直接复用；昇腾侧须按本节另选官方/现场 Harbor tag。  
4. 双路 64 核主要影响 **CPU 侧线程与 EasySearch/应用 worker 容量**，不改变 NPU 双芯切分逻辑（仍见 §5）。

现场确认命令：

```bash
uname -m
lscpu | sed -n '1,40p'
# 记录：Architecture、CPU(s)、Socket(s)、Core(s) per socket、Model name
```


### 1.3 默认必须部署 vs 不部署

| 组件 | 目录 | 是否默认必部 | 是否需要昇腾 AI 镜像 |
|------|------|--------------|----------------------|
| 宿主机 Docker / NPU 栈 | 宿主机 | **是** | — |
| EasySearch | `rag_db-deploy/` | **是** | 否 |
| vLLM | `vllm-deploy/` | **是** | **是** |
| MinerU | `mineru-deploy/` | **是**（RAG 扫描 PDF / 知识摄入） | **是** |
| 应用 API + Redis + MinIO | `app/app-deploy/` | **是** | **是**（嵌入/重排走 NPU） |
| Paddle 版面 OCR | `paddleocr-layout-deploy/` | **否** | — |
| Neo4j / GraphRAG | `graphrag_db-deploy/` | **否** | 否 |
| 小模型 GPU profile | `models-app-gpu` | **否** | — |

> 若业务后续启用检修 V0 或 GraphRAG，按英伟达/沐曦既有目录模式另增 Ascend overlay，不纳入本文默认路径。

### 1.4 仓库现状 vs 本方案目标

| 栈 | 现状（`dev_djs`） | 本方案目标 |
|----|------------------|------------|
| `vllm-deploy` | 已有 `docker-compose.ascend.yml` 骨架 + `deploy.sh --platform ascend`；**缺**默认昇腾 `BASE_IMAGE`、完整设备挂载说明与可用 `.env` 示例 | 完善 overlay，现场可 `./deploy.sh --platform ascend` |
| `app/app-deploy` | 仅 CPU / `docker-nvidia` / `docker-mx` | 新增 **`docker-ascend/`**（对齐沐曦目录结构） |
| `mineru-deploy` | 仅 CPU + **NVIDIA** GPU | 新增 Ascend GPU Dockerfile + compose |
| 宿主机文档 | 无 Atlas/Kylin V10 SP3 专项 | 以本文为准 |

未完成「仓库侧适配」前，可先完成宿主机 H 类工作与镜像/版本选型；**应用层升腾一键部署以代码合入为准**。

---

## 2. 总体架构与部署顺序

```text
┌─────────────────────────────────────────────────────────────────┐
│  Kylin V10 SP3 宿主机                                            │
│  NPU Driver/Firmware + Ascend Docker Runtime + Docker Compose    │
│  /aidata/{models,data,mineru,...}                                │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
  [1] rag-easysearch          （无 NPU）
         │
         ▼
  [2] vllm-service            （NPU：双芯主用于大模型，TP 视模型）
         │
         ▼
  [3] mineru-api              （NPU：可与 vLLM 分时或分芯）
         │
         ▼
  [4] models-app + redis + minio
      （NPU：嵌入/重排；与 vLLM 分芯或错峰）
```

**推荐顺序（必须）**：

```text
宿主机基础环境验收
  → EasySearch
  → 模型权重落盘
  → vLLM（ascend）
  → MinerU（ascend）
  → app 昇腾栈（接入外部网络）
  → 端到端冒烟
```

各栈通过 **Docker 外部网络** 互联（名称须与各目录 `.env` 一致），应用容器内访问：

| 依赖 | 推荐地址（容器内） |
|------|-------------------|
| vLLM | `http://vllm-service:8000/v1` |
| EasySearch | `https://rag-easysearch:9200` |
| MinerU | `http://mineru-api:8000` |
| Redis | `redis://models-app-redis:6379/0`（以 compose 服务名为准） |

**禁止**在应用容器内用 `127.0.0.1:<宿主机映射端口>` 访问其它容器。

---

## 3. 硬件与软件版本矩阵（落地前必填）

> Atlas / CANN / 镜像 **强版本绑定**。下表「现场填写」列在实施前由交付人员填实，并与华为支持包、制品库 tag 对齐。

| 类别 | 建议记录项 | 现场填写 |
|------|------------|----------|
| 卡 | Atlas 300I Duo，芯片数、单芯显存、PCIe | |
| CPU | 双路 × 32 核；主频；**Model name**；落地为 ARM 或 C86 | |
| OS | Kylin V10 SP3，`uname -r`（确认与 CPU 架构介质一致） | |
| CPU 架构 | `uname -m` → `x86_64`（C86）或 `aarch64`（ARM）；镜像 platform 必须同架构 | |
| NPU 驱动 | `Ascend-hdk-*-npu-driver_*.run` 版本（**含 arch 后缀**） | |
| NPU 固件 | `Ascend-hdk-*-npu-firmware_*.run` 版本 | |
| CANN | `ascend-toolkit` / kernels 版本（同架构） | |
| Ascend Docker Runtime | 包版本（同架构） | |
| vLLM 基础镜像 | 厂商或现场 Harbor 的 vLLM-Ascend / MindIE 镜像 tag（**amd64 或 arm64**） | |
| app 基础镜像 | 含 `torch_npu` 的推理镜像 tag（同架构） | |
| MinerU 基础镜像 | 可跑 MinerU + `torch_npu` 的镜像 tag（同架构） | |
| Docker | Engine + Compose V2 版本 | |

**选型原则（与沐曦栈一致）**：

- **国产卡**：`BASE_IMAGE` 使用厂商已适配 **PyTorch + vLLM（或等价推理栈）** 的镜像；应用侧仅装业务依赖（`VLLM_REQUIREMENTS_PROFILE=extras`），**禁止**用 PyPI 通用 `vllm`/CUDA `torch` 覆盖厂商栈。
- 镜像 OS 族优先与麒麟/RHEL 系一致时，继续走仓库已有 `Dockerfile-mx`（`yum/dnf`）路径；若官方镜像为 Ubuntu 系，则需单独 Ascend Dockerfile（`apt`），不要硬套 yum。

---

## 4. 宿主机基础环境部署方案

### 4.1 环境信息采集

```bash
cat /etc/os-release
uname -r
uname -m
lspci | grep -i -E 'huawei|processing|davinci|npu' || true
free -h
df -h
```

确认：

1. OS 为 Kylin V10 SP3，且安装介质与 **ARM / C86** 落地架构一致；
2. 内核版本在所选 **驱动 run 包** 支持列表内（不匹配需换驱动包或按华为文档安装 `kernel-devel` 后编译安装）；
3. **`uname -m` 与后续 Docker / 昇腾镜像架构一致**（C86→`x86_64`/`amd64`；ARM→`aarch64`/`arm64`）；
4. `lscpu` 可见双路、每路约 32 核（合计约 64 核，以现场为准）。

### 4.2 系统依赖（按驱动包文档安装）

典型依赖（名称以现场 yum 源为准）：

```bash
sudo yum install -y gcc gcc-c++ make perl pciutils \
  kernel-devel-$(uname -r) kernel-headers-$(uname -r) \
  dkms elfutils-libelf-devel 2>/dev/null || true
```

关闭可能干扰 NPU 的无关 GPU 驱动冲突策略按机房规范执行；安装前建议做驱动包自带的 `--check`（若支持）。

### 4.3 安装 NPU 驱动与固件

软件包从华为支持网站或现场交付介质获取，文件名形如：

- `Ascend-hdk-xxx-npu-driver_<version>_linux-<arch>.run`
- `Ascend-hdk-xxx-npu-firmware_<version>.run`

**顺序（务必按华为官方当前文档执行，下列为常见规则摘要）**：

| 场景 | 推荐顺序 |
|------|----------|
| 首次安装（无驱动或已卸载） | 多数文档：**先驱动，后固件**（以现场 HDK 手册为准） |
| 覆盖安装（已有驱动未卸载） | 多数文档：**先固件，后驱动** |

示例（版本号与 arch 替换为现场包）：

```bash
chmod +x Ascend-hdk-*-npu-driver_*.run Ascend-hdk-*-npu-firmware_*.run

# 示例：首次安装常见写法（以官方手册为准）
sudo ./Ascend-hdk-xxx-npu-driver_*.run --full --install-for-all
sudo ./Ascend-hdk-xxx-npu-firmware_*.run --full

sudo reboot
```

**验收**：

```bash
npu-smi info
# 期望：可见 NPU，芯片数与 Duo 一致（通常为 2），无异常 Error 码刷屏
ls -l /dev/davinci* /dev/davinci_manager /dev/devmm_svm /dev/hisi_hdc 2>/dev/null
```

将运维用户加入驱动相关组（常见 `HwHiAiUser`，以安装日志为准）：

```bash
# 示例，组名以现场为准
sudo usermod -aG HwHiAiUser "$USER"
# 重新登录后再生效
```

### 4.4 安装 Docker 与 Compose

Kylin V10 可按机房规范安装 Docker Engine，并确保 Compose **插件 V2**：

```bash
docker version
docker compose version
```

离线环境：在有网机器导出 rpm/deb 与 `docker-ce` 依赖，或使用经审批的一键安装包。

配置镜像加速/私有仓库（Harbor）写入 `/etc/docker/daemon.json`（按现场安全策略），然后：

```bash
sudo systemctl restart docker
sudo systemctl enable docker
```

### 4.5 Ascend 容器运行时与设备注入

目标：容器内能看到 NPU 设备并执行 `npu-smi`。

**方式 A（推荐，若现场提供）**：安装 **Ascend Docker Runtime**，在 `daemon.json` 注册默认或按 compose 指定 `runtime`。

**方式 B（与仓库国产卡 overlay 一致）**：compose 使用 `privileged: true` + 挂载 `/dev` 与驱动目录（当前 `docker-compose.ascend.yml` 骨架即此思路），并按华为「运行容器」文档补齐卷，例如：

```text
设备（示例）：
  /dev/davinci0、/dev/davinci1（Duo 双芯）
  /dev/davinci_manager、/dev/devmm_svm、/dev/hisi_hdc

卷（示例，路径以现场安装为准）：
  /usr/local/Ascend/driver
  /usr/local/dcmi
  /usr/local/bin/npu-smi
  /var/log/npu
```

**冒烟容器（镜像 tag 换成现场可用的昇腾基础镜像）**：

```bash
docker run --rm -it --privileged \
  --device=/dev/davinci0 \
  --device=/dev/davinci1 \
  --device=/dev/davinci_manager \
  --device=/dev/devmm_svm \
  --device=/dev/hisi_hdc \
  -v /usr/local/Ascend/driver:/usr/local/Ascend/driver \
  -v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
  <ascend-base-image:tag> \
  npu-smi info
```

容器内可见 NPU 信息即视为 **容器算力注入验收通过**。

### 4.6 CANN 与镜像内工具链

- 若选用的 **vLLM / PyTorch 镜像已内置匹配 CANN**，宿主机可只保留驱动+runtime，避免双份 CANN 冲突。
- 若镜像要求宿主机 CANN 挂载进容器，则严格按镜像说明 `source .../set_env.sh`，并保证 **驱动 ↔ CANN ↔ 镜像** 版本矩阵一致。

### 4.7 EasySearch 内核参数

```bash
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-easysearch.conf
sudo sysctl --system
sysctl vm.max_map_count
```

### 4.8 目录规划（建议）

```bash
sudo mkdir -p \
  /aidata/models/llm \
  /aidata/models/embeddings/Qwen3-Embedding-0.6B \
  /aidata/models/reranker/Qwen3-Reranker-0.6B \
  /aidata/mineru/models \
  /aidata/mineru/io \
  /aidata/data/redis_data \
  /aidata/data/minio_data \
  /aidata/data/session_storage \
  /aidata/data/easysearch

# 权限按实际运行用户调整
sudo chown -R "$USER:$USER" /aidata
```

磁盘建议：模型与 EasySearch 索引分盘或预留充足空间（视模型体量，通常数百 GB 级起步）。

### 4.9 CPU 侧容量建议（双路 64 核）

NPU 承担大模型与（目标态）嵌入/重排/MinerU 算力后，CPU 仍承担：EasySearch、Redis、MinIO、应用异步、分词/预处理、以及 **方案 B 下的 CPU 嵌入/重排**。建议：

| 组件 | 建议 |
|------|------|
| EasySearch | 按官方建议设置 JVM 堆；避免把全部 64 核都打满给 ES，预留应用与系统核 |
| MinerU | `.env` 中 `OMP_NUM_THREADS` / `TORCH_NUM_THREADS` 等勿默认拉满 64；可从 8～16 起测，再压测调优 |
| models-app | uvicorn/`workers` 按并发与内存定，通常远小于 64；与 NPU 推理并发解耦 |
| 系统 | 保留若干核给 OS、Docker、监控，避免整体 steal 过高 |

主频 ≥2.0GHz 满足常见信创服务器基线；**对昇腾驱动/镜像选型无额外分支**，只需在验收报告中记录实际主频与型号。

### 4.10 宿主机基础环境验收清单

| 检查项 | 命令/标准 |
|--------|-----------|
| OS / 内核 | Kylin V10 SP3；内核在驱动支持列表；介质与 CPU 架构一致 |
| CPU | 双路、约 32 核/路；落地 ARM 或 C86 已记录；`uname -m` 明确 |
| NPU | `npu-smi info` 正常，双芯可见 |
| 设备节点 | `/dev/davinci*` 等存在 |
| Docker | Engine + `docker compose` 可用 |
| 容器 NPU | 冒烟容器内 `npu-smi info` 成功；容器镜像架构正确 |
| sysctl | `vm.max_map_count=262144` |
| 目录 | `/aidata/...` 已建 |

---

## 5. 双芯资源切分建议（96GB Duo）

Atlas 300I Duo 为 **两颗 NPU**。同机同时跑 vLLM + 嵌入/重排 + MinerU 时，必须显式切分，避免三栈抢同一芯导致 OOM。

### 5.1 推荐方案（生产默认）

| 服务 | `ASCEND_RT_VISIBLE_DEVICES` | 说明 |
|------|-----------------------------|------|
| vLLM | `0,1`（或仅 `0` 若模型单芯可放下） | 大模型为主；大模型需 TP=2 时占用双芯 |
| models-app 嵌入/重排 | 与 vLLM **错峰**：若 vLLM 占满双芯，则嵌入改 **CPU** 或错峰批处理；若 vLLM 仅占 `0`，则 app 用 `1` | 见下「方案 A/B」 |
| MinerU | 与高峰推理错峰；或单独窗口用 `1` | 知识摄入非实时路径优先错峰 |

**方案 A（吞吐优先，推荐有双芯且模型可单芯）**

- vLLM：`ASCEND_RT_VISIBLE_DEVICES=0`，`TENSOR_PARALLEL_SIZE=1`
- app：`ASCEND_RT_VISIBLE_DEVICES=1`，`EMBEDDING_DEVICE` / `RAG_RERANKER_DEVICE` 指向 NPU（设备名以镜像内 torch_npu 约定为准，常为 `npu:0` 映射到可见的那一张）
- MinerU：夜间/低峰使用 `1`，或临时停 app 重排占卡

**方案 B（大模型优先，双芯 TP）**

- vLLM：`ASCEND_RT_VISIBLE_DEVICES=0,1`，`TENSOR_PARALLEL_SIZE=2`
- app 嵌入/重排：**CPU**（仍用昇腾基础镜像亦可，但 device 配 CPU），或独立第二台机器
- MinerU：错峰或 CPU 模式（`docker-compose.cpu.yml`）作为降级

> 地面沉降现场模型选型未定时：先用方案 A 做联调；若 `models.yaml` 预设要求 TP=2，再切方案 B 并明确 app 不占 NPU。

### 5.2 与沐曦/英伟达变量对照

| 平台 | 可见设备变量 |
|------|----------------|
| 英伟达 | `CUDA_VISIBLE_DEVICES` / `NVIDIA_VISIBLE_DEVICES` |
| 沐曦 | `MX_VISIBLE_DEVICES` |
| **昇腾 Atlas** | **`ASCEND_RT_VISIBLE_DEVICES`** |

---

## 6. 默认必部应用：详细部署步骤

### 6.1 EasySearch（`rag_db-deploy`）

**不依赖 NPU。**

```bash
cd rag_db-deploy
cp .env.example .env
# 编辑：EASYSEARCH_PASSWORD、EASYSEARCH_DATA（建议 /aidata/data/easysearch）、端口等

docker compose --env-file .env -f docker-compose.easy.search.yml up -d
```

首次设置 admin 密码（若 401）：按 `rag_db-deploy/README.md` 在容器内执行安全 API 或 `reset_admin_password.sh`。

**验收**：

```bash
curl -k -u admin:<密码> "https://127.0.0.1:9200/_cluster/health?pretty"
```

应用侧后续必须一致：`RAG_ES_USERNAME` / `RAG_ES_PASSWORD` / `RAG_ES_HOSTS=https://rag-easysearch:9200`。

---

### 6.2 模型权重准备

| 用途 | 建议宿主机路径 | 备注 |
|------|----------------|------|
| 大模型 | `/aidata/models/llm/<模型目录>` | 与 `MODEL_PRESET` / `models.yaml` 一致 |
| 嵌入 | `/aidata/models/embeddings/Qwen3-Embedding-0.6B` | 与 app `.env.example` 默认一致 |
| 重排 | `/aidata/models/reranker/Qwen3-Reranker-0.6B` | 同上 |
| MinerU | `/aidata/mineru/models/...` | 见 `mineru-deploy/README.md` 离线约定 |

离线环境使用 ModelScope/HF 预下载后 rsync；注意 git-lfs。

昇腾上部分权重格式/算子与 CUDA 不完全一致，**以所选 vLLM-Ascend 镜像支持的模型列表为准** 选择 `MODEL_PRESET`。

---

### 6.3 vLLM（`vllm-deploy`，昇腾）

#### 6.3.1 现状

- 平台 overlay：`docker/docker-compose.ascend.yml`
- 启动：`./deploy.sh --platform ascend` 或 `.env` 中 `VLLM_PLATFORM=ascend`
- 构建：国产路径使用 `docker/Dockerfile-mx` + `VLLM_REQUIREMENTS_PROFILE=extras`

#### 6.3.2 目标配置（`.env` 示例）

```env
# 替换为现场昇腾 vLLM 镜像（勿用沐曦/CUDA 镜像）
BASE_IMAGE=<registry>/vllm-ascend:<tag-for-atlas300i-duo-kylinv10>

VLLM_REQUIREMENTS_PROFILE=extras
VLLM_PLATFORM=ascend
VLLM_IMAGE=vllm-service:ascend

MODEL_PRESET=<与 models.yaml 中昇腾可用预设一致>
MODEL_PATH=/aidata/models/llm

ASCEND_RT_VISIBLE_DEVICES=0,1
TENSOR_PARALLEL_SIZE=2
# 若方案 A 单芯：ASCEND_RT_VISIBLE_DEVICES=0 且 TENSOR_PARALLEL_SIZE=1

VLLM_HOST=0.0.0.0
VLLM_PORT=8000
```

#### 6.3.3 完善 overlay 时建议补齐（仓库改造项 C1）

在 `docker-compose.ascend.yml` 中相对当前骨架，建议对齐华为容器运行文档：

- 明确 `BASE_IMAGE` 默认值（现场 Harbor tag）；
- 按需增加精确 `--device` / 驱动目录 volume（收敛 `privileged`+全量 `/dev` 的范围，满足安全要求时）；
- 文档中写明 Kylin V10 SP3 + Atlas 300I Duo 的验证命令。

#### 6.3.4 启动

```bash
cd vllm-deploy
cp .env.example .env
# 按 6.3.2 修改

chmod +x deploy.sh
./deploy.sh --platform ascend
# 等价：
# cd docker && docker compose --env-file ../.env \
#   -f docker-compose.yml -f docker-compose.ascend.yml up -d --build
```

**验收**：

```bash
curl -s "http://127.0.0.1:8000/health"
curl -s "http://127.0.0.1:8000/v1/models"
# 容器内（若已挂载 npu-smi）
docker exec -it vllm-service npu-smi info
```

应用侧：`LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1`，`LLM_DEFAULT_MODEL` 与 served model name 一致。

---

### 6.4 MinerU（`mineru-deploy`，昇腾）

#### 6.4.1 现状

- GPU 路径仅为 **NVIDIA**（`Dockerfile.gpu` + `docker-compose.gpu.yml` + `runtime: nvidia`）。
- **无** Ascend compose；落地前需完成改造项 C3。

#### 6.4.2 目标形态（对齐沐曦/vLLM 国产 overlay 思路）

新增例如：

- `Dockerfile.gpu.ascend`：`FROM` 昇腾 PyTorch/`torch_npu` 基础镜像，安装 `mineru[core]`（版本需验证昇腾兼容性）；
- `docker-compose.gpu.ascend.yml`：`privileged` + 设备/驱动挂载 + `ASCEND_RT_VISIBLE_DEVICES`；
- `.env`：`MINERU_DEVICE_MODE` 按 MinerU 在 NPU 上的实际取值（以适配结果为准，可能为自定义或先 CPU 降级）。

**降级策略**：若短期无法在 NPU 上稳定跑 MinerU，默认必部可先用 **`docker-compose.cpu.yml`** 保证功能，同时保留 C3 为性能项——但需在清单中注明「功能必部 / 性能待昇腾适配」。  
**本方案默认目标仍为 Ascend GPU**，与前期范围一致。

#### 6.4.3 启动（目标命令）

```bash
cd mineru-deploy
cp .env.example .env
# MINERU_MODELS_HOST_PATH=/aidata/mineru/models
# MINERU_IO_HOST_PATH=/aidata/mineru/io
# ASCEND_RT_VISIBLE_DEVICES=1   # 示例：与 vLLM 分芯

docker network create mineru-stack || true
docker compose --env-file .env -f docker-compose.gpu.ascend.yml up -d --build
```

**验收**：`http://<host>:<MINERU_PORT>/health`；应用 `MINERU_ENABLED=true`，`MINERU_BASE_URL=http://mineru-api:8000`，**`MINERU_IO_HOST_PATH` 与 app 挂载同一宿主机目录**。

---

### 6.5 应用栈（`app/app-deploy`，昇腾）

#### 6.5.1 现状

| 形态 | 路径 |
|------|------|
| CPU | `docker-compose.yml` + `Dockerfile` |
| 英伟达 | `docker-nvidia/` |
| 沐曦 | `docker-mx/` |
| **昇腾** | **无（需新增 `docker-ascend/`）** |

沐曦参考实现要点（昇腾应对齐）：

- 基础镜像含厂商适配 PyTorch；
- compose：`privileged` / 设备可见性 / 外部网络 `vllm-external`、`rag-external`、`mineru-external`；
- 嵌入/重排：`EMBEDDING_DEVICE`、`RAG_RERANKER_DEVICE`；
- Redis、MinIO 同栈启动。

#### 6.5.2 目标目录结构

```text
app/app-deploy/docker-ascend/
  Dockerfile-ascend          # FROM 昇腾 torch_npu 镜像，装 requirements-大模型应用.txt
  docker-compose-ascend.yml  # Redis + MinIO + models-app（+ 可选 profile）
  README.md                  # 麒麟 + Atlas 启动说明
```

#### 6.5.3 `.env` 关键项（与平台无关但必配）

```env
SERVICE_API_KEYS=<本地生成密钥>

LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1
LLM_DEFAULT_MODEL=<与 vLLM served name 一致>

RAG_VECTOR_STORE_TYPE=es
RAG_ES_HOSTS=https://rag-easysearch:9200
RAG_ES_USERNAME=admin
RAG_ES_PASSWORD=<与 EasySearch 一致>

EMBEDDING_MODEL_PATH=/workspace/models/embeddings/Qwen3-Embedding-0.6B
RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
# 设备：按 torch_npu 实际字符串配置，例如 npu:0（相对 ASCEND_RT_VISIBLE_DEVICES 映射）
EMBEDDING_DEVICE=npu:0
RAG_RERANKER_DEVICE=npu:0

MINERU_ENABLED=true
MINERU_BASE_URL=http://mineru-api:8000
MINERU_IO_HOST_PATH=/aidata/mineru/io

REDIS_DATA_HOST_PATH=/aidata/data/redis_data
# MinIO / 会话等路径按 .env.example

ASCEND_RT_VISIBLE_DEVICES=1
```

网络名与 `VLLM_DOCKER_NETWORK`、`RAG_DOCKER_NETWORK`、`MINERU_DOCKER_NETWORK` 必须与已创建外部网络一致。

#### 6.5.4 启动（目标命令）

```bash
cd app/app-deploy
cp .env.example .env
# 编辑关键项

# 确认外部网络已存在
docker network ls

cd docker-ascend
cp ../.env .env
docker compose --env-file .env -f docker-compose-ascend.yml up -d --build
```

**验收**：

```bash
curl -s "http://127.0.0.1:${APP_PORT:-8083}/health"   # 以实际健康路径为准
docker logs models-app 2>&1 | tail -n 100
# 确认嵌入/重排加载日志，无 CUDA 残留错误
```

业务请求头：`Authorization: Bearer <SERVICE_API_KEYS>`。

---

## 7. 端到端冒烟用例

| 序号 | 用例 | 期望 |
|------|------|------|
| 1 | `npu-smi info`（宿主机） | 双芯正常 |
| 2 | EasySearch `_cluster/health` | yellow/green |
| 3 | vLLM `/v1/models` | 返回目标模型 |
| 4 | MinerU `/health` | 200 |
| 5 | models-app 健康检查 | 200 |
| 6 | 带 API Key 的简单 chat/LLM 调用 | 有有效回复 |
| 7 | RAG 摄入一篇文本 + 问答 | 召回与回答合理 |
| 8 | （若启用）扫描 PDF 经 MinerU 再摄入 | 产出 Markdown 且可检索 |
| 9 | 观察 NPU 占用是否符合切分方案 | 无意外双栈同芯打满 OOM |

---

## 8. 日常运维与回滚

### 8.1 常用命令

```bash
# 查看 NPU
npu-smi info

# 分栈日志（服务名以现场为准）
docker logs -f vllm-service
docker logs -f mineru-api
docker logs -f models-app
docker logs -f rag-easysearch
```

### 8.2 建议停栈顺序

```text
app（models-app / redis / minio）→ MinerU → vLLM → EasySearch
```

数据卷（`/aidata/data`、模型目录）默认保留；仅删容器不删卷。

### 8.3 配置与密钥

- 各目录 `.env` **勿提交 Git**；
- 变更业务环境变量后重启 `models-app`；变更 compose 端口/卷后 `up -d` 重建。

### 8.4 备份要点

| 内容 | 路径示例 |
|------|----------|
| EasySearch 数据 | `EASYSEARCH_DATA` / `/aidata/data/easysearch` |
| Redis / MinIO | `/aidata/data/redis_data`、`minio_data` |
| 模型 | `/aidata/models`、`/aidata/mineru/models` |
| 配置 | 各 `*/.env`、`vllm-deploy/config/*.yaml` |

详见 `deploy-docs/项目容器本地挂载和备份说明.md`。

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| 驱动与 Kylin V10 SP3 内核不匹配 | 安装前核对 HDK 支持列表；准备匹配内核的 driver 包 |
| **标书写 ARM/C86，现场架构未锁定就拉镜像** | 先 `uname -m` / `lscpu` 锁定路线；版本矩阵填「ARM 或 C86」后再下载驱动与镜像 |
| **C86 机误用 arm64 镜像（或相反）** | 一律同架构；出现 `exec format error` 即查镜像 platform |
| 镜像架构与主机不一致 | `uname -m` 与镜像 `amd64/arm64` 对齐；驱动 run 包 arch 后缀一致 |
| 用 CUDA/沐曦镜像跑昇腾 | 禁止；必须换 Ascend 镜像与 `ASCEND_*` 变量 |
| 三栈抢双芯 OOM | 执行 §5 切分；大模型 TP=2 时 app 改 CPU 或错峰 |
| CPU 线程打满 64 核拖垮延迟 | 按 §4.9 限制 ES/MinerU/应用线程与 worker |
| MinerU 昇腾适配周期长 | 功能先 CPU 保交付，性能跟 C3 |
| vLLM Ascend 对某模型不支持 | 换 `MODEL_PRESET`；更新 `models.yaml` 注释限制 |
| 容器内无设备 | 复查 runtime、`--device`、驱动卷、用户组 |

---

## 10. 仓库改造任务说明书（供开发落地）

与 [`工作清单-华为Atlas300IDuo.md`](./工作清单-华为Atlas300IDuo.md) 中 C1–C5 对应：

1. **C1 vLLM**：为 `docker-compose.ascend.yml` 增加默认可替换的 `BASE_IMAGE`、完善设备/驱动挂载注释；`.env.example` 增加 Atlas 300I Duo + Kylin V10 SP3 注释段；README 增加昇腾章节（可引用本文）。
2. **C2 app**：新增 `docker-ascend/`，以 `docker-mx` 为模板替换基础镜像、设备变量、README；主 `README.md`「部署形态选择」表增加昇腾一行。
3. **C3 MinerU**：新增 Ascend GPU Dockerfile/compose；README 增加「昇腾」小节；失败时文档写明 CPU 降级命令。
4. **C4 版本矩阵**：把现场最终 tag **与 ARM/C86 落地架构** 回填本文 §3。  
5. **C5 资源切分**：三份 `.env.example` 用注释写清方案 A/B 默认值；CPU 线程类变量参考 §4.9。

---

## 11. 文档与代码索引

| 资源 | 路径 |
|------|------|
| 本方案勾选清单 | `docs/基础环境及部署/工作清单-华为Atlas300IDuo.md` |
| 项目总运维手册 | `enterprise-level_transformation_docs/项目整体部署运维手册.md` |
| vLLM | `vllm-deploy/README.md`、`docker/docker-compose.ascend.yml` |
| EasySearch | `rag_db-deploy/README.md` |
| MinerU | `mineru-deploy/README.md` |
| 应用部署 | `app/app-deploy/README.md`、`docker-mx/`、`docker-nvidia/` |
| 外挂服务联调 | `app/app-deploy/README-external-services-lan-deploy.md` |

---

## 12. 附录：华为官方文档入口（实施时以最新版为准）

实施驱动/固件/容器时，请以华为支持网站对应 **Atlas 300I Duo** 与当前 HDK 版本手册为准，例如：

- Atlas 300I Duo NPU 驱动和固件安装指南  
- 昇腾软件安装指南中的「运行容器 / 多容器场景」  
- CANN 安装指南与版本配套表  

将实际使用的文档编号与版本号记入 §3 版本矩阵「现场填写」列，便于审计与升级。
