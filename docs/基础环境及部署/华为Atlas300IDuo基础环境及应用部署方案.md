# 华为 Atlas 300I Duo_96G 基础环境及应用部署方案

> **文档性质**：面向 `dev_djs`（地面沉降）项目在 **华为 Atlas 300I Duo_96G ×4 + 双路 32 核 ARM（aarch64）+ 银河麒麟 Kylin V10 SP3** 上的宿主机基础环境与默认必部应用部署方案。  
> **配套清单**：勾选式进度见 [`工作清单-华为Atlas300IDuo.md`](./工作清单-华为Atlas300IDuo.md)。  
> **对齐仓库**：`vllm-deploy/`、`rag_db-deploy/`、`mineru-deploy/`、`app/app-deploy/`；通用运维见 `enterprise-level_transformation_docs/项目整体部署运维手册.md`。  
> **版本**：2026-07（已固化：ARM + HDK 25.2.0 驱动/固件 + Ascend Docker Runtime 7.1.RC1 + CANN 8.2.RC1 经官方镜像提供 + 三栈统一 `vllm-ascend:v0.10.0rc1-310p`）。

---

## 1. 目标与范围

### 1.1 目标

在单台（或同构）**配备 4 张 Atlas 300I Duo_96G** 的推理服务器上，完成：

1. **宿主机基础环境**：RAID/分区、NPU 驱动/固件、Docker/Compose、容器 NPU 注入、目录与内核参数；  
2. **默认必部应用栈**：EasySearch → vLLM（昇腾）→ MinerU（昇腾）→ models-app（昇腾嵌入/重排）+ Redis + MinIO；  
3. **可验收、可回滚** 的配置约定与资源切分。

### 1.2 现场约束（已确认）

| 项 | 值 |
|----|-----|
| 加速卡 | 华为 **Atlas 300I Duo_96G × 4 张**（每张 Duo 双芯、单卡约 96GB HBM；整机约 **8 颗 NPU / 合计约 384GB**） |
| CPU | **双路**，单颗 **32 核**（合计约 **64** 核），主频 **≥2.0GHz** |
| CPU 架构 | **已锁定 ARM（`aarch64`）**；驱动/Docker/昇腾镜像一律 **arm64 / aarch64** |
| 操作系统 | **银河麒麟 Kylin V10 SP3**（ARM 安装介质） |
| 统一 AI 底座镜像 | **`quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`**（vLLM / MinerU 底座 / app 底座） |
| 镜像内 CANN | **8.2.RC1**（**宿主机不单独安装 CANN**，由官方 AI 镜像提供） |
| NPU 驱动 | **`Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run`**（Ascend HDK 25.2.0） |
| NPU 固件 | **`Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run`**（与上同页齐套） |
| 驱动/固件下载 | [昇腾社区 Firmware-Drivers（CANN 8.2.RC1 / HDK 25.2.0 / 300I Duo）](https://www.hiascend.com/hardware/firmware-drivers/community?product=2&model=17&cann=8.2.RC1&driver=Ascend+HDK+25.2.0) |
| Ascend Docker Runtime | **`Ascend-docker-runtime_7.1.RC1_linux-aarch64.run`** |
| Runtime 下载 | [MindCluster v7.1.RC1 Releases](https://gitcode.com/Ascend/mind-cluster/releases/v7.1.RC1) |
| 项目分支 | `dev_djs` |
| 设备可见性变量 | `ASCEND_RT_VISIBLE_DEVICES` |

#### 1.2.1 架构说明（已锁定 ARM）

| 落地 | `uname -m` | 镜像 platform | 本现场 |
|------|------------|---------------|--------|
| **ARM** | **`aarch64`** | **`linux/arm64`** | **已采用** |

硬性规则：

1. 驱动 `.run`、Docker 静态包、`vllm-ascend:…-310p` 均须 **aarch64/arm64**，禁止混用 amd64。  
2. 麒麟安装介质须为 ARM 版。  
3. 双路 64 核影响 CPU 侧线程与 worker，不改变 §5 四卡切分。  

确认命令：

```bash
uname -m    # 期望：aarch64
lscpu | sed -n '1,40p'
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
│  Kylin V10 SP3 · ARM aarch64                                     │
│  Atlas 300I Duo_96G ×4 · Driver 25.2.0 + Firmware 7.7.0.6.236   │
│  Docker/Compose · Ascend Docker Runtime 7.1.RC1                   │
│  统一底座：vllm-ascend:v0.10.0rc1-310p（内含 CANN 8.2.RC1）       │
│  /aidata/{models,data,mineru,...} · /opt/deploy                  │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
  [1] rag-easysearch          （无 NPU）
         │
         ▼
  [2] vllm-service            （底座镜像直接用；设备 0,1,2,3＝卡0+卡1）
         │
         ▼
  [3] mineru-api              （同底座 + MinerU NPU 层；设备 6＝卡3）
         │
         ▼
  [4] models-app + redis + minio
      （同底座 + 业务依赖；设备 4,5＝卡2 嵌入/重排）
```

**推荐顺序（必须）**：

```text
RAID 确认 + 引导分区已就绪
  → 系统 OK 后补齐 §4.0 剩余分区并 fstab
  → 安装 NPU 驱动/固件（§4.3，指定包名）
  → 安装 Docker/Compose（§4.4 在线或离线）
  → 安装 Ascend Docker Runtime 7.1.RC1（§4.5）
  → 拉取统一镜像 vllm-ascend:v0.10.0rc1-310p
  → EasySearch → 模型落盘 → vLLM → MinerU → app
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

## 3. 硬件与软件版本矩阵（已固化）

> 下列版本已与 **`vllm-ascend:v0.10.0rc1-310p`（内含 CANN 8.2.RC1）** 对齐；实施时按包名下载，**勿擅自换成「更新」驱动/镜像破坏配套**。

| 类别 | 固化值 |
|------|--------|
| 卡 | Atlas 300I Duo_96G **×4**（约 8 逻辑 NPU） |
| CPU | 双路 × 32 核；**ARM `aarch64`** |
| OS | Kylin V10 SP3（ARM） |
| NPU 驱动 | **`Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run`**（Ascend HDK **25.2.0**） |
| NPU 固件 | **`Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run`** |
| 驱动/固件下载 | https://www.hiascend.com/hardware/firmware-drivers/community?product=2&model=17&cann=8.2.RC1&driver=Ascend+HDK+25.2.0 |
| CANN | **8.2.RC1** — **不在宿主机单独安装**；由官方 AI 镜像提供 |
| 统一底座镜像 | **`quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`**（[tags](https://quay.io/repository/ascend/vllm-ascend?tab=tags)） |
| vllm-deploy | 同上镜像（直接或 `BASE_IMAGE` + `extras`） |
| app-deploy | 同上镜像作 **BASE_IMAGE**，再装业务依赖 |
| mineru-deploy | 同上镜像作 **BASE_IMAGE**，按 MinerU Ascend/`npu.Dockerfile` 构建业务层 |
| Docker 离线包 | **`docker-20.10.24.tgz`**（aarch64）+ **`docker-compose-linux-aarch64`**（见 §4.4） |
| Ascend Docker Runtime | **`Ascend-docker-runtime_7.1.RC1_linux-aarch64.run`**（见 §4.5） |
| Runtime 下载 | https://gitcode.com/Ascend/mind-cluster/releases/v7.1.RC1 |

**配套关系（摘要）**：

```text
宿主机：Driver 25.2.0 + Firmware 7.7.0.6.236
        + Ascend-docker-runtime 7.1.RC1
容器内：vllm-ascend:v0.10.0rc1-310p → 内置 cann 8.2.rc1-310p + torch_npu
```

**原则**：

- 三栈 **共用同一底座 tag**；app / MinerU **再构建业务层**，禁止 pip 覆盖为 CUDA `torch`。  
- Ubuntu 系镜像叠加业务层时用 **apt**，勿硬套 yum 的 `Dockerfile-mx`。  
- 镜像拉取：`docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`（国内可用 `m.daocloud.io/quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`）；麒麟直拉失败时按 FAQ 离线 `docker save/load`。

### 3.1 版本变更原则

现场已固化上表。若未来升级镜像或 HDK，必须 **整线更换**（驱动 + 固件 + Docker Runtime + 镜像），并重新验收；禁止只升其中一项。

---

## 4. 宿主机基础环境部署方案

### 4.0 RAID 与磁盘分区方案

本节为现场存储规划；**安装系统时已完成引导分区，其余在系统就绪后按目标表补齐**。

#### 4.0.1 RAID（已规划 / 须确认已做）

| 磁盘组 | 成员盘 | RAID 级别 | 可用容量（约） | 用途 |
|--------|--------|-----------|----------------|------|
| 组1 | 2 × 480GB SSD | **RAID1** | **480GB** | 系统、Docker、热数据（`/aidata/data` 等） |
| 组2 | 2 × 600GB SAS | **RAID1** | **600GB** | 模型、MinerU、MinIO 大文件、备份、部署代码 |

验收：

```bash
# 以现场 RAID 卡工具或 mdadm 为准，确认两套 RAID1 均 Optimal/正常
cat /proc/mdstat 2>/dev/null || true
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
```

#### 4.0.2 现场安装阶段已完成的分区

机房/装机侧说明（已落实）：

> 整个 **1G 的 `/boot`**，**512M 的引导（`/boot/efi`）**；**剩余空间等系统 OK 后再由部署侧划分**。

| 挂载点 | 大小 | 状态 | 所在盘（目标） |
|--------|------|------|----------------|
| `/boot/efi` | **512MB** | **已完成** | SSD RAID1 |
| `/boot` | **1GB** | **已完成** | SSD RAID1 |
| 其余分区 | — | **待系统就绪后划分** | 见下表 |

装机后先确认：

```bash
df -h /boot /boot/efi
lsblk -f
```

#### 4.0.3 系统就绪后的目标分区（在已完成 boot 基础上继续）

**SSD RAID1（约 480GB）— 系统与热数据**

| 挂载点 | 大小 | 内容 |
|--------|------|------|
| `/boot/efi` | 512MB | 引导（**已完成，勿改**） |
| `/boot` | 1GB | 内核（**已完成，勿改**） |
| `swap` | 16GB | 交换分区 |
| `/` | 70GB | 系统、昇腾驱动等 |
| `/var/lib/docker` | 130GB | Docker 镜像与容器层 |
| `/aidata/data` | **260GB**（约剩余） | EasySearch 本地数据、Redis、会话等热数据 |

> 容量按 512M+1G+16G+70G+130G+260G ≈ 477.5GB 对齐约 480GB；若实际可用略少，优先保证 `/` 与 `/var/lib/docker`，再压缩 `/aidata/data`。

**SAS/HDD RAID1（约 600GB）— 模型与大文件**

| 挂载点 | 大小 | 内容 |
|--------|------|------|
| `/aidata/models` | 260GB | 大模型、嵌入、重排权重 |
| `/aidata/mineru` | 80GB | MinerU 模型与 IO |
| `/aidata/data/minio_data` | 80GB | 上传文件、图片、PDF 等对象存储 |
| `/aidata/backup` | 80GB | 备份 |
| `/opt/deploy` | 100GB | 项目代码与配置 |

> 260+80+80+80+100 = 600GB。若实际可用不足，优先保证 `/aidata/models`，再压缩 backup / deploy。

#### 4.0.4 补分区操作要点（系统 OK 后）

1. **先摸清现状**：`lsblk`、`df -h`、`vgs/lvs`（若用 LVM）。若当前 `/` 已占满 SSD 剩余空间，需 **缩减根分区或改用 LVM 再切分**，避免直接删盘；生产机操作前做快照/备份。  
2. **推荐顺序**：创建 `swap` → 调整/固定 `/` 至约 70GB → 独立挂载 `/var/lib/docker` → 挂载 `/aidata/data` → 在 SAS 盘上依次创建并挂载 models / mineru / minio_data / backup / deploy。  
3. **写入 `/etc/fstab`**，`mount -a` 无报错后再装 Docker（保证 Docker 数据目录落在独立分区）。  
4. **权限**：部署用户对 `/aidata`、`/opt/deploy` 可写；Docker 目录按 root/docker 组惯例。  
5. 子目录创建见 **§4.8**（须在对应挂载点就绪后执行）。

示例（设备名须换成现场 `lsblk` 结果，下列仅为结构示意）：

```bash
# 查看
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,UUID

# 挂载点
sudo mkdir -p /var/lib/docker /aidata/data \
  /aidata/models /aidata/mineru /aidata/data/minio_data \
  /aidata/backup /opt/deploy

# 格式化与挂载示例（不要照抄设备名）
# sudo mkfs.xfs /dev/<ssd_part_docker>
# sudo mkfs.xfs /dev/<ssd_part_aidata_data>
# sudo mkfs.xfs /dev/<sas_part_models>
# ... 写入 fstab 后：
# sudo mount -a
df -h
```

#### 4.0.5 分区与本项目路径对应

| 本项目用途 | 宿主机路径 | 所在分区 |
|------------|------------|----------|
| EasySearch / Redis / 会话等 | `/aidata/data/...` | SSD `/aidata/data` |
| MinIO | `/aidata/data/minio_data` | SAS 独立分区（挂到该路径） |
| LLM / 嵌入 / 重排 | `/aidata/models/...` | SAS `/aidata/models` |
| MinerU | `/aidata/mineru/...` | SAS `/aidata/mineru` |
| 仓库与 compose 配置 | `/opt/deploy/...` | SAS `/opt/deploy` |
| Docker 镜像 | `/var/lib/docker` | SSD 独立分区 |

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

1. OS 为 Kylin V10 SP3，且为 **ARM** 安装介质；
2. **`uname -m` 输出 `aarch64`**（本现场已锁定 ARM，禁止用 amd64 驱动/镜像）；
3. 内核版本在 **HDK 25.2.0 / 驱动 25.2.0** 支持列表内（不匹配按华为文档安装 `kernel-devel` 或换配套内核）；
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

> **已固化包名**（与 CANN 8.2.RC1 / `vllm-ascend:v0.10.0rc1-310p` 配套；**勿换「更新」版本**）。  
> 下载页：[昇腾社区 Firmware-Drivers（CANN 8.2.RC1 / HDK 25.2.0 / 300I Duo）](https://www.hiascend.com/hardware/firmware-drivers/community?product=2&model=17&cann=8.2.RC1&driver=Ascend+HDK+25.2.0)

| 组件 | 固化包名 |
|------|----------|
| 驱动 | **`Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run`** |
| 固件 | **`Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run`** |

**顺序（务必按华为官方当前文档执行，下列为常见规则摘要）**：

| 场景 | 推荐顺序 |
|------|----------|
| 首次安装（无驱动或已卸载） | 多数文档：**先驱动，后固件**（以现场 HDK 手册为准） |
| 覆盖安装（已有驱动未卸载） | 多数文档：**先固件，后驱动** |

示例（首次安装常见写法，以官方手册为准）：

```bash
chmod +x Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run \
         Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run

sudo ./Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run --full --install-for-all
sudo ./Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run --full

sudo reboot
```

**验收**：

```bash
npu-smi info
# 期望：可见 4 张 Duo 卡对应的 NPU（常见为 8 个 Chip/逻辑设备），无异常 Error 码刷屏
# 同时记录：NPU ID（物理卡）与 Chip/Device 编号的对应关系，供 §5 切分填写
ls -l /dev/davinci* /dev/davinci_manager /dev/devmm_svm /dev/hisi_hdc 2>/dev/null
# 常见：/dev/davinci0 … /dev/davinci7（以现场为准）
```

将运维用户加入驱动相关组（常见 `HwHiAiUser`，以安装日志为准）：

```bash
# 示例，组名以现场为准
sudo usermod -aG HwHiAiUser "$USER"
# 重新登录后再生效
```

### 4.4 安装 Docker 与 Compose

> **前置**：§4.0 中 `/var/lib/docker` 独立分区建议已挂载；未挂载则先装后迁数据，或装前在 `daemon.json` 指定 `data-root`。  
> **目标**：`docker`（Engine）可用，且可用 **`docker compose`（推荐）** 或至少 `docker-compose`。  
> **架构**：本现场为 **ARM `aarch64`**；离线包使用 **`docker-20.10.24.tgz`（aarch64）** + **`docker-compose-linux-aarch64`**。

本方案提供两种安装方式：**在线一键**（可临时上网）与 **离线静态包（方案 A，信创/专网推荐）**。

#### 4.4.1 方式一：在线一键安装（轩辕脚本）

适用于能访问外网（或能访问 `xuanyuan.cloud` 及脚本所用镜像源）的麒麟环境。仓库既有说明亦采用该入口：

```bash
bash <(wget -qO- https://xuanyuan.cloud/docker.sh)
# 若无 wget、有 curl，可用：
# bash <(curl -fsSL https://xuanyuan.cloud/docker.sh)
```

说明与注意：

| 项 | 说明 |
|----|------|
| 作用 | 自动识别麒麟等系统，安装 Docker CE，并尝试安装 Compose |
| 风险 | 远程脚本直接执行，生产/信创需评估供应链；会改写 `/etc/docker/daemon.json`（镜像加速等） |
| Compose | 脚本常优先安装独立二进制 `docker-compose`（如 1.29.x）；**不保证**一定有 Compose V2 插件 |
| 验收 | 必须执行下文 §4.4.3；若仅有 `docker-compose` 而无 `docker compose`，可再按 §4.4.2 只补装 Compose 二进制 |

装完后建议核对 Docker 根目录是否落在规划分区：

```bash
docker info 2>/dev/null | grep -i "Docker Root Dir"
# 期望类似：Docker Root Dir: /var/lib/docker
```

#### 4.4.2 方式二：离线静态包安装（方案 A，推荐）

**本现场固化制品（ARM / aarch64）：**

| 组件 | 文件名 | 获取来源（有网机下载） |
|------|--------|------------------------|
| Docker Engine | **`docker-20.10.24.tgz`** | `https://download.docker.com/linux/static/stable/aarch64/docker-20.10.24.tgz`（可用华为云/阿里云等 `docker-ce/linux/static/stable/aarch64/` 镜像） |
| Docker Compose | **`docker-compose-linux-aarch64`** | GitHub `docker/compose` Releases（选与交付一致的 V2 版本资产名） |

将上述两个文件拷到目标机（示例目录 `/opt/deploy/offline-docker/`）。

**（1）安装 Docker Engine 20.10.24**

```bash
cd /opt/deploy/offline-docker
tar -zxvf docker-20.10.24.tgz
sudo cp docker/* /usr/bin/
# 确认
docker -v
# 期望含 20.10.24
```

编写 systemd 服务（若系统尚无 `docker.service`）：

```bash
sudo tee /etc/systemd/system/docker.service > /dev/null <<'EOF'
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target firewalld.service
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
LimitNOFILE=infinity
LimitNPROC=infinity
LimitCORE=infinity
TimeoutStartSec=0
Delegate=yes
KillMode=process
Restart=on-failure
StartLimitBurst=3
StartLimitInterval=60s

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/docker.socket > /dev/null <<'EOF'
[Unit]
Description=Docker Socket for the API

[Socket]
ListenStream=/var/run/docker.sock
SocketMode=0660
SocketUser=root
SocketGroup=docker

[Install]
WantedBy=sockets.target
EOF

sudo groupadd -f docker
sudo systemctl daemon-reload
```

**（2）安装 Compose（`docker-compose-linux-aarch64`）**

同时提供 `docker compose`（CLI 插件）与 `docker-compose` 命令，兼容本仓库文档两种写法：

```bash
cd /opt/deploy/offline-docker
sudo chmod +x docker-compose-linux-aarch64

# Compose V2 插件（推荐：docker compose ...）
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo cp docker-compose-linux-aarch64 /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# 兼容旧命令 docker-compose
sudo cp docker-compose-linux-aarch64 /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

**（3）daemon.json 与启动（离线/在线装完后均建议执行）**

按现场安全策略配置镜像加速或内网 Harbor；**数据目录指向已挂载的 `/var/lib/docker`**：

```bash
sudo mkdir -p /etc/docker /var/lib/docker
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "data-root": "/var/lib/docker",
  "registry-mirrors": [
    "https://docker.xuanyuan.me",
    "https://mirror.ccs.tencentyun.com",
    "https://docker.mirrors.ustc.edu.cn"
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
EOF

sudo systemctl enable --now docker.socket docker.service
# 可选：当前用户免 root
# sudo usermod -aG docker "$USER"   # 重新登录后生效
```

> 纯内网无外网镜像时，删除或改写 `registry-mirrors`，改为现场 Harbor。昇腾 runtime 等字段在 §4.5 再合并写入，避免互相覆盖。

#### 4.4.3 安装验收

```bash
docker version
docker compose version          # 推荐有输出
docker-compose version          # 离线方案应有输出；在线一键通常也有
systemctl is-active docker
docker info | grep -i "Docker Root Dir"
docker run --rm hello-world     # 离线环境需事先 docker load 本地 hello-world 镜像，可跳过
```

| 检查项 | 通过标准 |
|--------|----------|
| Engine | `docker version` 显示 Client/Server；离线方案 Server 为 **20.10.24** |
| Compose | 至少 `docker compose version` 或 `docker-compose version` 其一成功；**本仓库命令以 `docker compose` 为准** |
| 数据目录 | Root Dir 为 `/var/lib/docker`（且该路径在独立分区上） |
| 服务 | `systemctl is-active docker` → `active` |

### 4.5 Ascend Docker Runtime 与设备注入

目标：容器内能看到 NPU 设备并执行 `npu-smi`。本现场 **明确安装 Ascend Docker Runtime 7.1.RC1**（推荐主路径）；若 Runtime 不可用，再用方式 B 手工挂设备兜底。

官方安装说明参考：[Ascend Docker Runtime 手动安装（MindCluster 7.1.RC1）](https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/clustersched/dlug/dlug_installation_017.html)。

#### 4.5.1 方式 A（本现场必须）：安装 Ascend Docker Runtime 7.1.RC1

**前置**：§4.3 驱动/固件已装且 `npu-smi` 正常；§4.4 Docker 已安装并可 `systemctl` 管理。

| 项 | 固化值 |
|----|--------|
| 包名 | **`Ascend-docker-runtime_7.1.RC1_linux-aarch64.run`** |
| 下载 | [gitcode Ascend/mind-cluster Releases v7.1.RC1](https://gitcode.com/Ascend/mind-cluster/releases/v7.1.RC1) |
| 默认安装路径 | `/usr/local/Ascend`（可用 `--install-path` 改，须绝对路径） |
| 默认 Docker 配置 | `/etc/docker/daemon.json`（非默认路径时加 `--config-file-path=`） |

安装（默认路径）：

```bash
# 将 run 包放到目标机，例如 /opt/deploy/offline-ascend/
cd /opt/deploy/offline-ascend
chmod u+x Ascend-docker-runtime_7.1.RC1_linux-aarch64.run

# 安装（会改写 /etc/docker/daemon.json，注册 ascend runtime）
sudo ./Ascend-docker-runtime_7.1.RC1_linux-aarch64.run --install

# 若 daemon.json 不在默认路径：
# sudo ./Ascend-docker-runtime_7.1.RC1_linux-aarch64.run --install \
#   --config-file-path=/path/to/daemon.json

# 使配置生效
sudo systemctl daemon-reload
sudo systemctl restart docker
```

安装到指定目录示例：

```bash
sudo ./Ascend-docker-runtime_7.1.RC1_linux-aarch64.run --install \
  --install-path=/usr/local/Ascend
```

安装成功后，`daemon.json` 中通常会出现类似内容（安装器自动合并；**勿手工删掉已有 `data-root` / 镜像加速**，冲突时以现场合并结果为准）：

```json
{
  "default-runtime": "ascend",
  "runtimes": {
    "ascend": {
      "path": "/usr/local/Ascend/Ascend-Docker-Runtime/ascend-docker-runtime",
      "runtimeArgs": []
    }
  }
}
```

同时会生成默认挂载清单（常见路径）：`/etc/ascend-docker-runtime.d/base.list`。

**验收**：

```bash
docker info | grep -i -E 'Runtimes|Default Runtime'
# 期望类似：
#   Runtimes: ascend runc
#   Default Runtime: ascend

# 用默认 ascend runtime 冒烟（无需再写一长串 --device；仍建议先 pull 底座）
docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
docker run --rm --runtime=ascend \
  quay.io/ascend/vllm-ascend:v0.10.0rc1-310p \
  npu-smi info
```

> 若 `Default Runtime` 已是 `ascend`，compose/`docker run` 可不显式写 `--runtime=ascend`；显式写上更清晰。

卸载（需要时，以官方手册为准）：

```bash
sudo ./Ascend-docker-runtime_7.1.RC1_linux-aarch64.run --uninstall
sudo systemctl daemon-reload && sudo systemctl restart docker
```

#### 4.5.2 方式 B（兜底）：compose 手工挂设备

仅在 Runtime 安装失败或策略不允许改 `default-runtime` 时使用。compose 使用 `privileged: true` + 挂载 `/dev` 与驱动目录（当前 `docker-compose.ascend.yml` 骨架即此思路），并按华为「运行容器」文档补齐卷，例如：

```text
设备（示例，四卡常见 8 设备；按现场 npu-smi / ls /dev/davinci* 裁剪）：
  /dev/davinci0 … /dev/davinci7
  /dev/davinci_manager、/dev/devmm_svm、/dev/hisi_hdc

卷（示例，路径以现场安装为准）：
  /usr/local/Ascend/driver
  /usr/local/dcmi
  /usr/local/bin/npu-smi
  /var/log/npu
```

兜底冒烟：

```bash
docker run --rm -it --privileged \
  --device=/dev/davinci0 \
  --device=/dev/davinci1 \
  --device=/dev/davinci_manager \
  --device=/dev/devmm_svm \
  --device=/dev/hisi_hdc \
  -v /usr/local/Ascend/driver:/usr/local/Ascend/driver \
  -v /usr/local/bin/npu-smi:/usr/local/bin/npu-smi \
  quay.io/ascend/vllm-ascend:v0.10.0rc1-310p \
  npu-smi info
```

容器内可见 NPU 信息即视为 **容器算力注入验收通过**。

### 4.6 CANN 与镜像内工具链（宿主机不装 CANN）

本现场策略（已固化）：

| 项 | 约定 |
|----|------|
| CANN 版本 | **8.2.RC1** |
| 安装位置 | **仅在官方 AI 镜像内**（`vllm-ascend:v0.10.0rc1-310p`） |
| 宿主机 | **驱动 + 固件（§4.3）+ Ascend Docker Runtime 7.1.RC1（§4.5）**；**不单独安装** `ascend-toolkit` / CANN |

验收时核对容器内 CANN 与宿主机驱动配套即可，例如：

```bash
docker run --rm quay.io/ascend/vllm-ascend:v0.10.0rc1-310p \
  bash -lc 'ls /usr/local/Ascend/ascend-toolkit 2>/dev/null; cat /usr/local/Ascend/ascend-toolkit/*/version.cfg 2>/dev/null | head'
# 期望可见 cann 8.2.rc1 相关标识（以镜像实际路径为准）
```

禁止在宿主机再装一套不同版本 CANN 并强行挂进容器，以免与镜像内工具链冲突。

### 4.7 EasySearch 内核参数

```bash
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-easysearch.conf
sudo sysctl --system
sysctl vm.max_map_count
```

### 4.8 目录规划（须在 §4.0 挂载就绪后）

在对应分区已挂载的前提下创建子目录（勿把大模型写到未挂载的空目录导致占满根分区）：

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
  /aidata/data/easysearch \
  /aidata/backup \
  /opt/deploy

# 权限按实际运行用户调整
sudo chown -R "$USER:$USER" /aidata /opt/deploy
```

确认挂载正确：

```bash
df -h / /var/lib/docker /aidata/data /aidata/models /aidata/mineru /aidata/data/minio_data /aidata/backup /opt/deploy
```

### 4.9 CPU 侧容量建议（双路 64 核）

NPU 承担大模型与（目标态）嵌入/重排/MinerU 算力后，CPU 仍承担：EasySearch、Redis、MinIO、应用异步、分词/预处理等。建议：

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
| OS / 内核 | Kylin V10 SP3（ARM）；内核在 HDK 25.2.0 支持列表 |
| RAID / 分区 | 两套 RAID1 正常；`/boot` `/boot/efi` 已挂载；§4.0 目标挂载均已 `df -h` 可见 |
| CPU | 双路、约 32 核/路；**`uname -m` = `aarch64`** |
| NPU 驱动/固件 | 驱动 **25.2.0**、固件 **7.7.0.6.236**；`npu-smi` 可见 **4 卡 / ~8 设备** |
| CANN | **不在宿主机安装**；容器内为镜像自带 **8.2.RC1** |
| 设备节点 | `/dev/davinci0`…`/dev/davinci7`（以现场为准）等存在 |
| Docker | Engine + `docker compose`（或 `docker-compose`）可用；Root Dir=`/var/lib/docker` |
| Ascend Docker Runtime | **`Ascend-docker-runtime_7.1.RC1`**；`docker info` 显示 `Default Runtime: ascend`；`--runtime=ascend` 容器内 `npu-smi` 成功 |
| 统一镜像 | 已 `docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p` |
| sysctl | `vm.max_map_count=262144` |
| 目录 | `/aidata/...`、`/opt/deploy` 已建 |

---

## 5. 四卡资源切分方案（Atlas 300I Duo_96G ×4）

本节为现场 **唯一明确的资源切分方案**（按物理卡隔离三栈）。

### 5.1 拓扑约定

| 项 | 约定（须用现场 `npu-smi info` 核对后改号） |
|----|---------------------------------------------|
| 物理卡 | **4 张** Atlas 300I Duo_96G |
| 单卡 | Duo = **2 颗** Ascend 芯片，单卡 HBM ≈ **96GB** |
| 整机 | 约 **8 个逻辑 NPU 设备**，合计 HBM ≈ **384GB** |
| 设备号 | 下文默认按连续编号 **`0..7`**（卡0→`0,1`，卡1→`2,3`，卡2→`4,5`，卡3→`6,7`） |

```text
物理卡0 (≈96GB)     物理卡1 (≈96GB)     物理卡2 (≈96GB)     物理卡3 (≈96GB)
  dev 0,1              dev 2,3              dev 4,5              dev 6,7
        └──── vLLM ────┘                    └ models-app ┘      └ MinerU ┘
```

> 若现场 `npu-smi` 按「NPU ID / Chip ID」展示且与上表不一致，**以现场编号改写本节所有 `ASCEND_RT_VISIBLE_DEVICES`**，切分原则不变：三栈 **按物理卡隔离**，禁止设备号重叠。

### 5.2 切分表（必须执行）

| 服务 | 物理卡 | `ASCEND_RT_VISIBLE_DEVICES` | `TENSOR_PARALLEL_SIZE` / 设备 | 说明 |
|------|--------|-----------------------------|-------------------------------|------|
| **vLLM** | 卡0 + 卡1 | `0,1,2,3` | 默认 `2`；更大模型且镜像支持时可设 `4` | 大模型主算力；约 192GB 级显存池 |
| **models-app** | 卡2 | `4,5` | 嵌入 `npu:0`、重排 `npu:1`（相对容器可见集） | Qwen3 嵌入/重排常驻，与推理隔离 |
| **MinerU** | 卡3 | `6` | — | 知识摄入；设备 `7` 预留，默认不占用 |

同机同时跑上述三栈时，必须按上表配置；**禁止**三栈都写 `0,1` 或互相重叠。

### 5.3 `.env` 配置（与上表一致）

```env
# vllm-deploy/.env
ASCEND_RT_VISIBLE_DEVICES=0,1,2,3
TENSOR_PARALLEL_SIZE=2
# 更大模型且镜像支持时：TENSOR_PARALLEL_SIZE=4

# app/app-deploy/.env（昇腾栈）
ASCEND_RT_VISIBLE_DEVICES=4,5
EMBEDDING_DEVICE=npu:0
RAG_RERANKER_DEVICE=npu:1

# mineru-deploy/.env
ASCEND_RT_VISIBLE_DEVICES=6
```

> 容器内 `npu:0` / `npu:1` 是 **可见设备集合内的相对编号**，不是宿主机全局号。app 仅暴露 `4,5` 时，容器内第一张仍是 `npu:0`。

联调时可临时让 vLLM 只用 `0,1` 验证通路，确认后再恢复为上表的 `0,1,2,3`。

### 5.4 与沐曦/英伟达变量对照

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

#### 6.3.1 现状与底座

- 平台 overlay：`docker/docker-compose.ascend.yml`
- 启动：`./deploy.sh --platform ascend` 或 `.env` 中 `VLLM_PLATFORM=ascend`
- **统一底座镜像（已固化）**：`quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`（[tags](https://quay.io/repository/ascend/vllm-ascend?tab=tags)）
- 构建：若需业务 extras，可用 `Dockerfile-mx` 思路，但 **Ubuntu 系底座请用 apt Dockerfile**，勿硬套 yum；`VLLM_REQUIREMENTS_PROFILE=extras` 时禁止覆盖为 CUDA `torch` / 通用 `vllm`

拉取：

```bash
docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
# 国内：docker pull m.daocloud.io/quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
```

本栈可 **几乎直接使用该镜像** 作为 `BASE_IMAGE` / 运行镜像（按需加 `extras`）。

#### 6.3.2 目标配置（`.env` 示例）

```env
BASE_IMAGE=quay.io/ascend/vllm-ascend:v0.10.0rc1-310p

VLLM_REQUIREMENTS_PROFILE=extras
VLLM_PLATFORM=ascend
VLLM_IMAGE=vllm-service:ascend

MODEL_PRESET=<与 models.yaml 中昇腾可用预设一致>
MODEL_PATH=/aidata/models/llm

ASCEND_RT_VISIBLE_DEVICES=0,1,2,3
TENSOR_PARALLEL_SIZE=2
# 更大模型且镜像支持时：TENSOR_PARALLEL_SIZE=4（仍仅使用设备 0..3，见 §5）

VLLM_HOST=0.0.0.0
VLLM_PORT=8000
```

#### 6.3.3 完善 overlay 时建议补齐（仓库改造项 C1）

在 `docker-compose.ascend.yml` 中相对当前骨架，建议：

- 默认 `BASE_IMAGE=quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`；
- 按需增加精确 `--device` / 驱动目录 volume（收敛 `privileged`+全量 `/dev`）；
- 文档写明 Kylin V10 SP3 ARM + Atlas 300I Duo 的验证命令。

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
docker exec -it vllm-service npu-smi info
```

应用侧：`LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1`，`LLM_DEFAULT_MODEL` 与 served model name 一致。

---

### 6.4 MinerU（`mineru-deploy`，昇腾）

#### 6.4.1 现状

- GPU 路径仅为 **NVIDIA**（`Dockerfile.gpu` + `docker-compose.gpu.yml` + `runtime: nvidia`）。
- **无** Ascend compose；落地前需完成改造项 C3。

#### 6.4.2 目标形态（统一底座 + MinerU 业务层）

**底座（与 vLLM/app 相同）**：

```bash
docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
```

新增例如：

- `Dockerfile.gpu.ascend`：`FROM quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`，按 Ascend 官方 MinerU / `npu.Dockerfile` 思路安装 NPU 侧 MinerU（Ubuntu 底座用 **apt**）；保护 `torch_npu`，禁止换成 CUDA `torch`；
- `docker-compose.gpu.ascend.yml`：`privileged` + 设备/驱动挂载 + `ASCEND_RT_VISIBLE_DEVICES=6`；
- Duo 上建议尝试 `--enforce-eager --dtype float16`（以适配结果为准）；
- `.env`：`MINERU_DEVICE_MODE` 按 MinerU 在 NPU 上的实际取值配置。

**降级策略**：若短期无法在 NPU 上稳定跑 MinerU，可用 **`docker-compose.cpu.yml`** 保功能，同时保留 C3——清单中注明「功能必部 / 性能待昇腾适配」。  
**本方案默认目标仍为 Ascend GPU**。

#### 6.4.3 启动（目标命令）

```bash
cd mineru-deploy
cp .env.example .env
# MINERU_MODELS_HOST_PATH=/aidata/mineru/models
# MINERU_IO_HOST_PATH=/aidata/mineru/io
# ASCEND_RT_VISIBLE_DEVICES=6   # §5：独占卡3；勿与 vLLM/app 重叠
# BASE_IMAGE=quay.io/ascend/vllm-ascend:v0.10.0rc1-310p

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

- **底座**：`FROM quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`（内含 CANN 8.2.RC1 + `torch_npu`）；
- 业务层用 **pip + python3** 装 `requirements-大模型应用.txt` 等，**保护 `torch_npu`**，禁止换成 CUDA `torch`；Ubuntu 底座用 **apt**，勿硬套 `Dockerfile-mx` 的 yum；
- compose：`privileged` / 设备可见性 / 外部网络 `vllm-external`、`rag-external`、`mineru-external`；
- 嵌入/重排：`EMBEDDING_DEVICE`、`RAG_RERANKER_DEVICE`；
- Redis、MinIO 同栈启动。

拉取底座：

```bash
docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
```

#### 6.5.2 目标目录结构

```text
app/app-deploy/docker-ascend/
  Dockerfile-ascend          # FROM quay.io/ascend/vllm-ascend:v0.10.0rc1-310p + 业务依赖
  docker-compose-ascend.yml  # Redis + MinIO + models-app（+ 可选 profile）
  README.md                  # 麒麟 ARM + Atlas 启动说明
```

#### 6.5.3 `.env` 关键项（与平台无关但必配）

```env
SERVICE_API_KEYS=<本地生成密钥>

# 构建时使用（若 Dockerfile ARG）
BASE_IMAGE=quay.io/ascend/vllm-ascend:v0.10.0rc1-310p

LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1
LLM_DEFAULT_MODEL=<与 vLLM served name 一致>

RAG_VECTOR_STORE_TYPE=es
RAG_ES_HOSTS=https://rag-easysearch:9200
RAG_ES_USERNAME=admin
RAG_ES_PASSWORD=<与 EasySearch 一致>

EMBEDDING_MODEL_PATH=/workspace/models/embeddings/Qwen3-Embedding-0.6B
RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
# 设备：相对 ASCEND_RT_VISIBLE_DEVICES 可见集（§5：宿主机 4,5 → 容器内 npu:0 / npu:1）
EMBEDDING_DEVICE=npu:0
RAG_RERANKER_DEVICE=npu:1

MINERU_ENABLED=true
MINERU_BASE_URL=http://mineru-api:8000
MINERU_IO_HOST_PATH=/aidata/mineru/io

REDIS_DATA_HOST_PATH=/aidata/data/redis_data
# MinIO / 会话等路径按 .env.example

ASCEND_RT_VISIBLE_DEVICES=4,5
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
| 1 | `npu-smi info`（宿主机） | **4 张卡 / 约 8 设备**正常；与 §5 编号表一致 |
| 2 | EasySearch `_cluster/health` | yellow/green |
| 3 | vLLM `/v1/models` | 返回目标模型 |
| 4 | MinerU `/health` | 200 |
| 5 | models-app 健康检查 | 200 |
| 6 | 带 API Key 的简单 chat/LLM 调用 | 有有效回复 |
| 7 | RAG 摄入一篇文本 + 问答 | 召回与回答合理 |
| 8 | （若启用）扫描 PDF 经 MinerU 再摄入 | 产出 Markdown 且可检索 |
| 9 | 观察 NPU 占用是否符合 §5 切分表 | **三栈设备号无重叠**；无意外 OOM |

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
| 驱动与 Kylin V10 SP3 内核不匹配 | 安装前核对 HDK 25.2.0 支持列表；准备匹配内核 |
| **误用 amd64 驱动/镜像** | 本现场已锁定 **ARM**；一律 `aarch64` / `linux/arm64`；`exec format error` 即查 architecture |
| 驱动/固件/镜像版本漂移 | 严格使用 §3 固化包名与 `v0.10.0rc1-310p`；升级须整线更换 |
| 宿主机再装一套 CANN | **禁止**；CANN 8.2.RC1 仅来自官方 AI 镜像 |
| 用 CUDA/沐曦/`800I-A3` 等非 310p 镜像 | 禁止；统一 `…-310p` 底座 |
| 三栈设备号重叠导致 OOM / 互相挤占 | 严格执行 §5 切分表；禁止三栈设备号重叠 |
| 设备号与物理卡映射和文档不一致 | 以现场 `npu-smi` 重绘 §5.1 表后再改 `.env` |
| 大模型 TP 与可见设备数不匹配 | `TENSOR_PARALLEL_SIZE` ≤ 可见 NPU 个数（本节为 ≤4），且为镜像支持值 |
| CPU 线程打满 64 核拖垮延迟 | 按 §4.9 限制 ES/MinerU/应用线程与 worker |
| MinerU 昇腾适配周期长 | 功能先 CPU 保交付，性能跟 C3 |
| vLLM Ascend 对某模型不支持 | 换 `MODEL_PRESET`；更新 `models.yaml` 注释限制 |
| 容器内无设备 | 复查 runtime、`--device`、驱动卷、用户组 |
| app/MinerU 构建覆盖 `torch_npu` | Dockerfile 钉住厂商包；禁止 `pip install torch`（CUDA） |

---

## 10. 仓库改造任务说明书（供开发落地）

与 [`工作清单-华为Atlas300IDuo.md`](./工作清单-华为Atlas300IDuo.md) 中 C1–C5 对应：

1. **C1 vLLM**：`BASE_IMAGE` 默认 `quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`；完善设备/驱动挂载；`.env.example` / README 增加 Atlas 300I Duo + Kylin ARM 说明。
2. **C2 app**：新增 `docker-ascend/`，`FROM` 同上底座 + 业务依赖（apt）；主 README「部署形态」增加昇腾一行。
3. **C3 MinerU**：新增 Ascend GPU Dockerfile/compose，底座同上；失败时写明 CPU 降级。
4. **C4 版本矩阵**：已固化于本文 §3（驱动 25.2.0 / 固件 7.7.0.6.236 / CANN 8.2.RC1 经镜像 / tag `v0.10.0rc1-310p`）；代码侧默认值与文档一致即可。  
5. **C5 资源切分**：三份 `.env.example` 按 §5（vLLM `0,1,2,3` / app `4,5` / MinerU `6`）写清注释；CPU 线程类变量参考 §4.9。

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

本现场已固化版本见 **§3**。实施驱动/固件/容器时，仍以华为支持网站对应手册核对步骤细节：

- Atlas 300I Duo NPU 驱动和固件安装指南（对应 **HDK 25.2.0**）  
- 昇腾「运行容器 / 多容器场景」  
- [Firmware-Drivers 社区下载页（CANN 8.2.RC1 / HDK 25.2.0）](https://www.hiascend.com/hardware/firmware-drivers/community?product=2&model=17&cann=8.2.RC1&driver=Ascend+HDK+25.2.0)  
- [Ascend Docker Runtime 7.1.RC1 包](https://gitcode.com/Ascend/mind-cluster/releases/v7.1.RC1)（`Ascend-docker-runtime_7.1.RC1_linux-aarch64.run`）  
- [Ascend Docker Runtime 手动安装说明](https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/clustersched/dlug/dlug_installation_017.html)  
- [vllm-ascend 镜像 tags](https://quay.io/repository/ascend/vllm-ascend?tab=tags)（本现场：`v0.10.0rc1-310p`）  

> 手册步骤细节可随官网更新，但 **包名与镜像 tag 以本文 §3 为准**，勿擅自升到「最新」。
