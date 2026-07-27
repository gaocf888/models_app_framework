# 项目部署手册 - 昇腾（Atlas 300I Duo · 地面沉降）

> **适用**：`dev_djs` 分支 · 华为 **Atlas 300I Duo_96G ×4** · 银河麒麟 **Kylin V10 SP3（ARM `aarch64`）**  
> **角色**：**独立一站式部署手册**（版本矩阵、下载入口、宿主机安装、应用上线、验收）  
> 本文已包含现场固化包名与下载路径；无需再依赖其它文档即可完成部署。  
> 可选扩展：工作勾选见 [`docs/基础环境及部署/工作清单-华为Atlas300IDuo.md`](docs/基础环境及部署/工作清单-华为Atlas300IDuo.md)；更细排障见 [`docs/基础环境及部署/华为Atlas300IDuo基础环境及应用部署方案.md`](docs/基础环境及部署/华为Atlas300IDuo基础环境及应用部署方案.md)。

---

## 1. 一页总览

### 1.1 要部署什么

| 顺序 | 组件 | 目录 | NPU |
|------|------|------|-----|
| 0 | 宿主机（RAID/驱动/Docker/Runtime） | 本机 | — |
| 1 | EasySearch | `rag_db-deploy/` | 否 |
| 2 | vLLM | `vllm-deploy/` | 设备 `0,1,2,3` |
| 3 | MinerU | `mineru-deploy/` | 设备 `6` |
| 4 | 应用 + Redis + MinIO | `app/app-deploy/docker-ascend/` | 设备 `4,5` |

**默认不部署**：Paddle 版面侧车、Neo4j/GraphRAG、`models-app-gpu` 小模型 profile。

### 1.2 推荐流水线

```text
确认 ARM + RAID/分区
  → 按 §2 下载制品 → NPU 驱动/固件 → Docker/Compose → Ascend Docker Runtime
  → 拉底座镜像 → 落盘模型 → EasySearch → vLLM → MinerU → app
  → 冒烟验收
```

### 1.3 硬性规则（先读）

1. **架构一律 `aarch64` / `linux/arm64`**，禁止 amd64 驱动或镜像。  
2. **驱动 + 固件 + Runtime + 镜像整线配套**，勿单独追「最新」。  
3. **宿主机不装 CANN**；CANN **8.2.RC1** 只在官方 AI 镜像内。  
4. **底座镜像统一**：`quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`（一般不必在 `.env` 写 `BASE_IMAGE`）。  
5. 容器互访用 **服务名**，禁止 `127.0.0.1:<宿主机端口>`。

确认架构：

```bash
uname -m    # 期望：aarch64
cat /etc/os-release
```

---

## 2. 版本矩阵与下载清单（独立交付）

> **用法**：有网机按本表下载 → 拷贝到目标机 `/opt/deploy/offline/`（或现场约定目录）→ 再按 §4 安装。  
> 包名、版本、下载入口以本表为准；换版本须整线重评。

### 2.1 环境与版本矩阵

| 类别 | 固化值 |
|------|--------|
| 卡 | Atlas 300I Duo_96G **×4**（约 **8** 逻辑 NPU / ~384GB） |
| CPU | 双路 ×32 核，≥2.0GHz，**ARM `aarch64`** |
| OS | Kylin V10 SP3（ARM 安装介质） |
| NPU 驱动 | `Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run` |
| NPU 固件 | `Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run` |
| Ascend Docker Runtime | `Ascend-docker-runtime_7.1.RC1_linux-aarch64.run` |
| CANN | **8.2.RC1**（仅镜像内，宿主机不装） |
| AI 底座镜像 | `quay.io/ascend/vllm-ascend:v0.10.0rc1-310p` |
| Docker Engine（离线） | `docker-20.10.24.tgz`（**aarch64**） |
| Docker Compose（离线） | `docker-compose-linux-aarch64`（Compose V2 二进制） |

```text
宿主机：Driver 25.2.0 + Firmware 7.7.0.6.236 + Runtime 7.1.RC1
容器内：vllm-ascend:v0.10.0rc1-310p → CANN 8.2.RC1 + torch_npu
```

### 2.2 制品下载路径（包名 + URL）

| 制品 | 文件名 / Tag | 下载入口 |
|------|----------------|----------|
| NPU 驱动 | `Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run` | [昇腾社区 Firmware-Drivers（CANN 8.2.RC1 / HDK 25.2.0 / 300I Duo）](https://www.hiascend.com/hardware/firmware-drivers/community?product=2&model=17&cann=8.2.RC1&driver=Ascend+HDK+25.2.0) |
| NPU 固件 | `Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run` | 同上页，选对应 firmware 包 |
| Ascend Docker Runtime | `Ascend-docker-runtime_7.1.RC1_linux-aarch64.run` | [gitcode Ascend/mind-cluster Releases · v7.1.RC1](https://gitcode.com/Ascend/mind-cluster/releases/v7.1.RC1) |
| Runtime 安装说明（官方） | — | [Ascend Docker Runtime 手动安装（MindCluster 7.1.RC1）](https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/clustersched/dlug/dlug_installation_017.html) |
| AI 底座镜像 | `quay.io/ascend/vllm-ascend:v0.10.0rc1-310p` | 官方 tags：[quay.io/ascend/vllm-ascend](https://quay.io/repository/ascend/vllm-ascend?tab=tags) |
| Docker Engine 离线包 | `docker-20.10.24.tgz` | `https://download.docker.com/linux/static/stable/aarch64/docker-20.10.24.tgz`（可用华为云/阿里云等 `docker-ce/linux/static/stable/aarch64/` 镜像） |
| Docker Compose 离线包 | `docker-compose-linux-aarch64` | [GitHub docker/compose Releases](https://github.com/docker/compose/releases)（选交付约定的 V2 版本，下载资产名含 `linux-aarch64`） |
| Docker 在线安装脚本（可选） | — | `https://xuanyuan.cloud/docker.sh` |

**镜像拉取命令（有网目标机或中转机）：**

```bash
# 官方
docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p

# 国内加速（DaoCloud 代理 quay）
docker pull m.daocloud.io/quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
docker tag m.daocloud.io/quay.io/ascend/vllm-ascend:v0.10.0rc1-310p \
  quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
```

**离线传镜像（专网）：**

```bash
# 有网机
docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
docker save -o vllm-ascend-v0.10.0rc1-310p.tar quay.io/ascend/vllm-ascend:v0.10.0rc1-310p

# 目标机
docker load -i vllm-ascend-v0.10.0rc1-310p.tar
```

### 2.3 建议离线目录结构

在有网机下载后，建议按下列结构打成交付包，整体拷到目标机：

```text
/opt/deploy/offline/
├── npu/
│   ├── Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run
│   └── Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run
├── docker/
│   ├── docker-20.10.24.tgz
│   └── docker-compose-linux-aarch64
├── runtime/
│   └── Ascend-docker-runtime_7.1.RC1_linux-aarch64.run
└── images/                          # 可选：专网用
    └── vllm-ascend-v0.10.0rc1-310p.tar
```

下载核对清单：

- [ ] 驱动 `.run`（aarch64 / 25.2.0）
- [ ] 固件 `.run`（7.7.0.6.236）
- [ ] Runtime `.run`（7.1.RC1 / aarch64）
- [ ] `docker-20.10.24.tgz`（确认是 **aarch64**，不是 x86_64）
- [ ] `docker-compose-linux-aarch64`
- [ ] 底座镜像已 pull，或已有 `docker save` 的 tar

---

## 3. 架构与四卡切分

```text
物理卡0 (dev 0,1)     物理卡1 (dev 2,3)     物理卡2 (dev 4,5)     物理卡3 (dev 6,7)
        └──────── vLLM ────────┘              └ models-app ┘      └ MinerU ┘
                                              (嵌入/重排)         (设备 6；7 预留)
```

| 服务 | `ASCEND_RT_VISIBLE_DEVICES` | 说明 |
|------|-----------------------------|------|
| vLLM | `0,1,2,3` | 默认 `TENSOR_PARALLEL_SIZE=2`（更大模型可试 4） |
| models-app | `4,5` | 容器内 `EMBEDDING_DEVICE=npu:0`、`RAG_RERANKER_DEVICE=npu:1` |
| MinerU | `6` | `MINERU_DEVICE_MODE=npu` |

> `npu:0` 是**容器可见集合内的相对编号**。app 只暴露 `4,5` 时，容器内第一张仍是 `npu:0`。

容器内推荐地址：

| 依赖 | 地址 |
|------|------|
| vLLM | `http://vllm-service:8000/v1` |
| EasySearch | `https://rag-easysearch:9200` |
| MinerU | `http://mineru-api:8000` |
| Redis | `redis://redis:6379/0`（以 compose 服务名为准） |

---

## 4. 宿主机基础环境

### 4.1 RAID 与分区

> **危险操作**：下列命令会改分区表 / 格式化磁盘。生产机先备份；**设备名必须用现场 `lsblk` 结果替换**，禁止照抄 `nvme0n1` / `sda` / `md0`。  
> **已完成勿动**：`/boot/efi`（512M）、`/boot`（1G）。

#### 4.1.1 目标规划

| RAID | 成员 | 用途 |
|------|------|------|
| RAID1 | 2×480G SSD | 系统、Docker、热数据 |
| RAID1 | 2×600G SAS | 模型、MinerU、MinIO、备份、代码 |

**SSD RAID1（约 480G）**

| 挂载点 | 约大小 | 状态 |
|--------|--------|------|
| `/boot/efi` | 512M | 装机已完成，勿改 |
| `/boot` | 1G | 装机已完成，勿改 |
| `swap` | 16G | 待补 |
| `/` | 70G | 待确认/调整 |
| `/var/lib/docker` | 130G | 待补 |
| `/aidata/data` | ~260G | 待补 |

**SAS RAID1（约 600G）**

| 挂载点 | 约大小 |
|--------|--------|
| `/aidata/models` | 260G |
| `/aidata/mineru` | 80G |
| `/aidata/data/minio_data` | 80G |
| `/aidata/backup` | 80G |
| `/opt/deploy` | 100G |

#### 4.1.2 摸清现状（必做）

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,UUID,MODEL
cat /proc/mdstat 2>/dev/null || true
df -hT
sudo fdisk -l
# 若有硬件 RAID 卡，再用厂商工具看阵列状态（如 storcli / ssacli / MegaCLI，以机房为准）
```

记下：

- SSD 虚拟盘 / `md` 设备名（下文记为 **`$SSD`**，例 `/dev/md0` 或 `/dev/sda`）
- SAS 虚拟盘 / `md` 设备名（下文记为 **`$SAS`**，例 `/dev/md1` 或 `/dev/sdb`）
- 当前 `/` 是否已占满 SSD 剩余空间（若已占满，须先缩根或改 LVM 再切分，**不可直接删根分区**）

```bash
# 确认引导分区仍在
df -h /boot /boot/efi
```

#### 4.1.3 RAID1（两种路径选一）

**路径 A：硬件 RAID（现场常见）**

在 BIOS / RAID 卡工具中建好两组 RAID1 后，OS 只看到两块虚拟盘。本步只需验收：

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
# 期望看到约 480G、约 600G 两块逻辑盘（名称因卡而异）
# 阵列状态以 RAID 卡工具显示 Optimal/正常为准
```

**路径 B：软件 RAID（`mdadm`，仅当无硬件 RAID、且盘仍是裸盘时）**

> 会清空成员盘数据。成员盘名用 `lsblk` 替换（示例：SSD=`nvme0n1`+`nvme1n1`，SAS=`sda`+`sdb`）。

```bash
# 依赖
sudo yum install -y mdadm 2>/dev/null || sudo apt-get install -y mdadm

# --- SSD 组：2×480G → /dev/md0 ---
sudo mdadm --create /dev/md0 --level=1 --raid-devices=2 /dev/nvme0n1 /dev/nvme1n1
# --- SAS 组：2×600G → /dev/md1 ---
sudo mdadm --create /dev/md1 --level=1 --raid-devices=2 /dev/sda /dev/sdb

# 等待同步（可后台进行，状态看 cat /proc/mdstat）
cat /proc/mdstat
watch -n 2 cat /proc/mdstat   # 可选

# 持久化阵列配置
sudo mdadm --detail --scan | sudo tee -a /etc/mdadm.conf
# 部分发行版：
# sudo mdadm --detail --scan | sudo tee -a /etc/mdadm/mdadm.conf
sudo dracut -f 2>/dev/null || true
```

完成后：`$SSD=/dev/md0`，`$SAS=/dev/md1`（以实际为准）。

#### 4.1.4 分区（`parted`）

> 下列按「SSD 上 **仅有** `/boot/efi` + `/boot`，其余未切；SAS 整盘未用」的理想情况编写。  
> 若 `/` 已存在且过大：先用 LVM/`parted`/`resize2fs` 或厂商工具缩根到约 70G，再在空闲区切 `swap` / docker / aidata；**不要**对已挂载的根分区 `mkfs`。

先设变量（**按现场改**）：

```bash
export SSD=/dev/md0    # 例：硬件 RAID 虚拟盘或 /dev/sda
export SAS=/dev/md1    # 例：/dev/sdb
lsblk "$SSD" "$SAS"
```

**（1）SSD：在空闲区补 swap / 根 / docker / aidata（示意）**

若 SSD 尚无根分区、需一次划满（全新机、未装系统时才整盘这样切；**已装机切勿对含 `/boot` 的盘 `mklabel`**）：

```bash
# !!! 仅空盘或确认可重建时使用 mklabel；已有 /boot/efi+/boot 的系统盘跳过 mklabel !!!
# sudo parted -s "$SSD" mklabel gpt
# sudo parted -s "$SSD" mkpart ESP fat32 1MiB 513MiB
# sudo parted -s "$SSD" set 1 esp on
# sudo parted -s "$SSD" mkpart BOOT ext4 513MiB 1537MiB
```

**已装机、boot 已完成时**：只在空闲空间继续建分区（起止扇区用 `parted print free` 核对）：

```bash
sudo parted "$SSD" print free
sudo parted "$SSD" unit GiB print free

# 以下起止为「在 boot 之后」的示意，必须按 print free 的 Free Space 改数字
# 假设空闲从约 1.5GiB 开始（512M+1G）：
sudo parted -s "$SSD" \
  mkpart SWAP linux-swap 1537MiB 17921MiB      # ~16G swap
sudo parted -s "$SSD" \
  mkpart ROOT xfs 17921MiB 89601MiB            # ~70G /
sudo parted -s "$SSD" \
  mkpart DOCKER xfs 89601MiB 222721MiB         # ~130G /var/lib/docker
sudo parted -s "$SSD" \
  mkpart AIDATA xfs 222721MiB 100%             # 剩余 → /aidata/data

sudo parted "$SSD" print
lsblk "$SSD"
```

分区号因现有分区数量而变。记下例如：

| 用途 | 示例设备（按 `lsblk` 改） |
|------|---------------------------|
| swap | `${SSD}p4` 或 `${SSD}4` |
| `/` | `${SSD}p5` |
| `/var/lib/docker` | `${SSD}p6` |
| `/aidata/data` | `${SSD}p7` |

> 命名规则：`/dev/md0` → 常为 `/dev/md0p1`；`/dev/sda` → `/dev/sda1`。以 `lsblk` 为准。

**（2）SAS：整盘五分区**

```bash
sudo parted "$SAS" print free

# 空盘才执行 mklabel；已有数据先停
sudo parted -s "$SAS" mklabel gpt
sudo parted -s "$SAS" \
  mkpart MODELS xfs 1MiB 260GiB
sudo parted -s "$SAS" \
  mkpart MINERU xfs 260GiB 340GiB
sudo parted -s "$SAS" \
  mkpart MINIO xfs 340GiB 420GiB
sudo parted -s "$SAS" \
  mkpart BACKUP xfs 420GiB 500GiB
sudo parted -s "$SAS" \
  mkpart DEPLOY xfs 500GiB 100%

sudo parted "$SAS" print
lsblk "$SAS"
```

记下例如：`${SAS}p1`→models，`p2`→mineru，`p3`→minio，`p4`→backup，`p5`→deploy。

#### 4.1.5 格式化、挂载、`fstab`

先设分区变量（**按现场改**）：

```bash
# SSD
export P_SWAP=${SSD}p4
export P_ROOT=${SSD}p5          # 若根已存在且已格式化，跳过对本设备的 mkfs
export P_DOCKER=${SSD}p6
export P_AIDATA=${SSD}p7
# SAS
export P_MODELS=${SAS}p1
export P_MINERU=${SAS}p2
export P_MINIO=${SAS}p3
export P_BACKUP=${SAS}p4
export P_DEPLOY=${SAS}p5

lsblk "$P_SWAP" "$P_ROOT" "$P_DOCKER" "$P_AIDATA" \
      "$P_MODELS" "$P_MINERU" "$P_MINIO" "$P_BACKUP" "$P_DEPLOY"
```

格式化：

```bash
sudo mkswap "$P_SWAP"
# 仅当根分区是新建空分区时：
# sudo mkfs.xfs -f "$P_ROOT"
sudo mkfs.xfs -f "$P_DOCKER"
sudo mkfs.xfs -f "$P_AIDATA"
sudo mkfs.xfs -f "$P_MODELS"
sudo mkfs.xfs -f "$P_MINERU"
sudo mkfs.xfs -f "$P_MINIO"
sudo mkfs.xfs -f "$P_BACKUP"
sudo mkfs.xfs -f "$P_DEPLOY"
```

创建挂载点并挂载：

```bash
sudo mkdir -p /var/lib/docker /aidata/data \
  /aidata/models /aidata/mineru /aidata/data/minio_data \
  /aidata/backup /opt/deploy

sudo swapon "$P_SWAP"
# 根分区若是新盘需按装机流程处理；已运行系统通常已挂载 /
sudo mount "$P_DOCKER" /var/lib/docker
sudo mount "$P_AIDATA" /aidata/data
sudo mount "$P_MODELS" /aidata/models
sudo mount "$P_MINERU" /aidata/mineru
sudo mount "$P_MINIO"  /aidata/data/minio_data
sudo mount "$P_BACKUP" /aidata/backup
sudo mount "$P_DEPLOY" /opt/deploy

df -hT
swapon --show
```

写入 `/etc/fstab`（**用 UUID，勿写死 `/dev/sdX`**）：

```bash
# 查看 UUID
sudo blkid "$P_SWAP" "$P_DOCKER" "$P_AIDATA" \
  "$P_MODELS" "$P_MINERU" "$P_MINIO" "$P_BACKUP" "$P_DEPLOY"

# 备份后追加（把下面 UUID=... 换成 blkid 输出）
sudo cp -a /etc/fstab /etc/fstab.bak.$(date +%F)
sudo tee -a /etc/fstab >/dev/null <<'EOF'
# --- Atlas 300I Duo 现场分区（示例，务必替换 UUID）---
UUID=<swap-uuid>    none                    swap    defaults        0 0
UUID=<docker-uuid>  /var/lib/docker          xfs     defaults        0 0
UUID=<aidata-uuid>  /aidata/data             xfs     defaults        0 0
UUID=<models-uuid>  /aidata/models           xfs     defaults        0 0
UUID=<mineru-uuid>  /aidata/mineru           xfs     defaults        0 0
UUID=<minio-uuid>   /aidata/data/minio_data  xfs     defaults        0 0
UUID=<backup-uuid>  /aidata/backup           xfs     defaults        0 0
UUID=<deploy-uuid>  /opt/deploy              xfs     defaults        0 0
EOF

# 验证（应无报错；已挂载的会提示 busy/already，可接受）
sudo mount -a
df -hT
swapon --show
```

> **再装 Docker**。确保 `docker info` 中 Root Dir 落在已挂载的 `/var/lib/docker`。

#### 4.1.6 业务子目录

```bash
sudo mkdir -p \
  /aidata/models/llm \
  /aidata/models/embeddings/Qwen3-Embedding-0.6B \
  /aidata/models/reranker/Qwen3-Reranker-0.6B \
  /aidata/mineru/models /aidata/mineru/io \
  /aidata/data/{redis_data,minio_data,session_storage,easysearch} \
  /aidata/backup /opt/deploy /opt/deploy/offline
sudo chown -R "$USER:$USER" /aidata /opt/deploy
```

#### 4.1.7 验收

```bash
cat /proc/mdstat 2>/dev/null || true
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
df -hT | grep -E 'boot|docker|aidata|deploy|Filesystem'
swapon --show
# 期望：/boot、/boot/efi、/、/var/lib/docker、/aidata/data、
#       /aidata/models、/aidata/mineru、/aidata/data/minio_data、
#       /aidata/backup、/opt/deploy 均可见且容量大致符合规划
```

### 4.2 NPU 驱动与固件

制品与下载见 **§2.2**（昇腾社区 Firmware-Drivers 页）。首次安装常见顺序（以官方 HDK 手册为准）：**先驱动，后固件**。

```bash
cd /opt/deploy/offline/npu   # 或你的实际存放目录
chmod +x Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run \
         Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run
sudo ./Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run --full --install-for-all
sudo ./Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run --full
sudo reboot
```

验收：

```bash
npu-smi info          # 期望约 4 卡 / 8 设备
ls -l /dev/davinci*
# 若容器访问设备报权限问题，将当前用户加入驱动安装创建的用户组（常见 HwHiAiUser）后重新登录
# sudo usermod -aG HwHiAiUser "$USER"
```

### 4.3 Docker 与 Compose

**架构**：离线包必须是 **aarch64**。  
**前置**：`/var/lib/docker` 分区建议已挂载。

#### 方式一：在线一键（可临时上网）

```bash
bash <(wget -qO- https://xuanyuan.cloud/docker.sh)
# 或：bash <(curl -fsSL https://xuanyuan.cloud/docker.sh)
```

说明：自动装 Docker CE，并常附带 `docker-compose`；生产/信创需评估远程脚本风险。装完必须做本节「验收」。

#### 方式二：离线静态包（方案 A，专网推荐）

下载见 **§2.2**：

| 组件 | 文件名 | 直链 / 入口 |
|------|--------|-------------|
| Docker Engine | `docker-20.10.24.tgz` | `https://download.docker.com/linux/static/stable/aarch64/docker-20.10.24.tgz` |
| Compose V2 | `docker-compose-linux-aarch64` | https://github.com/docker/compose/releases |

将两个文件放到 `/opt/deploy/offline/docker/` 后：

```bash
cd /opt/deploy/offline/docker
tar xzvf docker-20.10.24.tgz
sudo cp docker/* /usr/bin/
sudo chmod +x /usr/bin/docker*

# Compose：插件路径 + 兼容命令名
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo cp docker-compose-linux-aarch64 /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
sudo ln -sf /usr/local/lib/docker/cli-plugins/docker-compose /usr/local/bin/docker-compose

# systemd（若系统尚未有 docker.service，可新建）
sudo tee /etc/systemd/system/docker.service >/dev/null <<'EOF'
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target firewalld.service
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
LimitNOFILE=1048576
LimitNPROC=infinity
LimitCORE=infinity
TimeoutStartSec=0
Restart=on-failure
StartLimitBurst=3
StartLimitInterval=60s

[Install]
WantedBy=multi-user.target
EOF

sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json >/dev/null <<'EOF'
{
  "data-root": "/var/lib/docker",
  "registry-mirrors": [
    "https://docker.xuanyuan.me",
    "https://mirror.ccs.tencentyun.com",
    "https://docker.mirrors.ustc.edu.cn"
  ]
}
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
# 重新登录后 docker 免 sudo 生效
```

#### 验收（两种方式都要做）

```bash
docker version
docker compose version    # 或 docker-compose version
docker info 2>/dev/null | grep -i "Docker Root Dir"
# 期望：Docker Root Dir: /var/lib/docker
```

### 4.4 Ascend Docker Runtime 7.1.RC1

| 项 | 值 |
|----|-----|
| 包名 | `Ascend-docker-runtime_7.1.RC1_linux-aarch64.run` |
| 下载 | https://gitcode.com/Ascend/mind-cluster/releases/v7.1.RC1 |
| 官方安装说明 | https://www.hiascend.com/document/detail/zh/mindcluster/71RC1/clustersched/dlug/dlug_installation_017.html |

```bash
cd /opt/deploy/offline/runtime
chmod u+x Ascend-docker-runtime_7.1.RC1_linux-aarch64.run
sudo ./Ascend-docker-runtime_7.1.RC1_linux-aarch64.run --install
sudo systemctl daemon-reload && sudo systemctl restart docker
```

验收：

```bash
docker info | grep -iE 'Runtimes|Default Runtime'
# 期望含：Default Runtime: ascend
```

### 4.5 拉取 / 载入底座镜像（不装宿主机 CANN）

镜像 tag 与下载见 **§2.2**。有网直接 pull；专网用 `docker load`。

```bash
docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p
# 或：docker load -i /opt/deploy/offline/images/vllm-ascend-v0.10.0rc1-310p.tar

docker run --rm --runtime=ascend \
  quay.io/ascend/vllm-ascend:v0.10.0rc1-310p npu-smi info
```

### 4.6 EasySearch 内核参数

```bash
echo 'vm.max_map_count=262144' | sudo tee /etc/sysctl.d/99-easysearch.conf
sudo sysctl --system
```

---

## 5. 模型与代码落盘

建议代码在 `/opt/deploy`（本仓库），模型在 `/aidata/models`：

```text
/aidata/models/llm/<MODEL_PRESET 对应目录>
/aidata/models/embeddings/Qwen3-Embedding-0.6B/
/aidata/models/reranker/Qwen3-Reranker-0.6B/
/aidata/mineru/models/          # MinerU 权重
/aidata/mineru/io/              # 与 app 共享 IO
```

LLM / 嵌入 / 重排目录须与各栈 `.env`、compose 挂载一致（见各目录 `.env.example`）。

---

## 6. 应用部署（按顺序）

仓库建议放在 `/opt/deploy/models_app_framework`（以下命令相对仓库根）。

### 6.1 EasySearch（rag_db-deploy）

```bash
cd rag_db-deploy
cp .env.example .env
# 按需改管理员密码等；记下 RAG_ES_* 供 app 使用
docker compose --env-file .env -f docker-compose.easysearch.yml up -d
```

验收：`https://<host>:9200` 健康；admin 密码与后续 `RAG_ES_PASSWORD` 一致。

> 启动easysearch的容器后，进入容器，初始化设置固定密码
```text
# 1. 进入容器
docker exec -it rag-easysearch bash

# 2. 执行curl请求，设置密码
curl -X PUT \
  --cert /app/easysearch/config/admin.crt \
  --key /app/easysearch/config/admin.key \
  -H 'Content-Type: application/json' \
  -k \
  -d '{
    "password": "ChangeMe_123!", 
    "external_roles": ["admin"]
  }' \
  https://localhost:9200/_security/user/admin
```

验证可用性：
curl -k -u admin:ChangeMe_123! "https://127.0.0.1:9200/_cluster/health?pretty"


### 6.2 vLLM（vllm-deploy）

```bash
cd vllm-deploy
cp .env.example .env
```

关键环境变量配置项（与 `.env.example` 昇腾段一致，不同加速卡配置见该环境变量配置文件最上方）：

```env
VLLM_PLATFORM=ascend
VLLM_REQUIREMENTS_PROFILE=extras
ASCEND_RT_VISIBLE_DEVICES=0,1,2,3
TENSOR_PARALLEL_SIZE=2
MODEL_PRESET=<与 config/models.yaml 一致>
MODEL_PATH=/aidata/models/llm
```

> 底座默认在 `docker/Dockerfile-ascend`，一般不必写 `BASE_IMAGE`。

启动（`docker compose` overlay，**不用** `deploy.sh`）：

```bash
cd docker
docker compose --env-file ../.env \
  -f docker-compose.yml -f docker-compose.ascend.yml up -d --build
```

查看日志：

```bash
cd vllm-deploy/docker
docker compose -f docker-compose.yml -f docker-compose.ascend.yml logs -f
```

验收：

```bash
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/v1/models
```

### 6.3 MinerU（mineru-deploy）

```bash
cd mineru-deploy
cp .env.example .env
```

关键环境变量配置项：

```env
MINERU_DEVICE_MODE=npu
ASCEND_RT_VISIBLE_DEVICES=6
INSTALL_CUDA_TORCH=0
MINERU_MODELS_HOST_PATH=/aidata/mineru/models
MINERU_IO_HOST_PATH=/aidata/mineru/io
```

```bash
docker network create mineru-stack || true
# gpu模式运行（上述关键环境变量配置项针对GPU模式）-- 不同加速卡的docker-compose配置文件要对应上述关键配置项
docker compose --env-file .env -f docker-compose.gpu.ascend.yml up -d --build
# cpu模式运行（针对不支持mineru的加速卡环境，可使用cpu模式运行mineru）
docker compose --env-file .env -f docker-compose.cpu.yml up -d --build
```

验收：`http://127.0.0.1:8009/health`（宿主机端口以 `MINERU_PORT` 为准）。  
若 NPU 暂不稳定，可临时改用 `docker-compose.cpu.yml` 保功能。

### 6.4 应用栈（app/app-deploy）

```bash
cd app/app-deploy
cp .env.example .env
```

**必改 / 必核（昇腾相关）**：

```env
# 鉴权
SERVICE_API_KEYS=<本地生成密钥>

# LLM（容器间主机名）
LLM_DEFAULT_ENDPOINT=http://vllm-service:8000/v1
LLM_DEFAULT_MODEL=<与 vLLM served_model_name 一致>

# EasySearch（与 rag_db-deploy 一致）
RAG_ES_HOSTS=https://rag-easysearch:9200
RAG_ES_USERNAME=admin
RAG_ES_PASSWORD=<与 EasySearch 一致>
RAG_ES_VERIFY_CERTS=false

# 嵌入 / 重排 + 四卡切分（与「设备与可见卡」小节成对）
EMBEDDING_MODEL_PATH=/workspace/models/embeddings/Qwen3-Embedding-0.6B
RAG_RERANKER_MODEL_PATH=/workspace/models/rerank/Qwen3-Reranker-0.6B
EMBEDDING_DEVICE=npu:0
RAG_RERANKER_DEVICE=npu:1
ASCEND_RT_VISIBLE_DEVICES=4,5

# MinerU
MINERU_ENABLED=true
MINERU_BASE_URL=http://mineru-api:8000
MINERU_IO_HOST_PATH=/aidata/mineru/io

# Compose 网络与路径
APP_PORT=8083
VLLM_DOCKER_NETWORK=docker_vllm-network
RAG_DOCKER_NETWORK=ai-stack
MINERU_DOCKER_NETWORK=mineru-stack
EMBEDDING_MODELS_HOST_PATH=/aidata/models/embeddings
RERANKER_MODELS_HOST_PATH=/aidata/models/reranker
REDIS_DATA_HOST_PATH=/aidata/data/redis_data
MINIO_DATA_HOST_PATH=/aidata/data/minio_data
```

生成 API Key（在仓库根）：

```bash
PYTHONPATH=. python -c "from app.auth.keygen import generate_service_api_key; print(generate_service_api_key())"
```

启动：

```bash
# 占位网络（未部署侧车/人脸库时仍需存在，否则 compose 因 external 失败）
docker network create paddle-layout-stack 2>/dev/null || true
docker network create face-milvus-stack 2>/dev/null || true

# 启动  加速卡docker-compose配置文件与上方关键配置项对应
docker compose --env-file .env -f docker-ascend/docker-compose-ascend.yml up -d --build
```

验收：

```bash
curl -s "http://127.0.0.1:${APP_PORT:-8083}/health"
docker compose -f docker-ascend/docker-compose-ascend.yml logs -f models-app
```

业务请求头：`Authorization: Bearer <SERVICE_API_KEYS>`。  
更多业务项（NL2SQL、会话、综合分析等）见 `app/app-deploy/.env.example`。

---

## 7. 冒烟与验收清单

| # | 检查 | 期望 |
|---|------|------|
| 1 | `npu-smi info` | ~4 卡 / 8 设备；驱动 25.2.0、固件 7.7.0.6.236 |
| 2 | `docker info` | `Default Runtime: ascend` |
| 3 | EasySearch `_cluster/health` | yellow/green |
| 4 | vLLM `/v1/models` | 返回目标模型 |
| 5 | MinerU `/health` | 200 |
| 6 | models-app `/health` | 200 |
| 7 | 带 API Key 的简单对话 | 有效回复 |
| 8 | RAG 摄入 + 问答 | 召回合理 |
| 9 | （可选）扫描 PDF → MinerU → 摄入 | Markdown 可检索 |
| 10 | 三栈设备号 | **无重叠**（0–3 / 4–5 / 6） |

---

## 8. 日常运维

```bash
# NPU
npu-smi info

# 日志（示例）
docker logs -f vllm-service
docker logs -f mineru-api
cd app/app-deploy && docker compose -f docker-ascend/docker-compose-ascend.yml logs -f models-app
```

**建议停栈顺序**：app → MinerU → vLLM → EasySearch。  
数据卷（`/aidata/...`）默认保留；只删容器不删盘。

回滚要点：保留各目录 `.env`、模型目录、镜像 tag；升级须 **驱动 + 固件 + Runtime + 镜像** 整线更换（仍以 §2 矩阵为准）。

---

## 9. 相关文档（可选）

本文已可独立完成部署；下列为扩展阅读：

| 文档 | 用途 |
|------|------|
| `docs/基础环境及部署/工作清单-华为Atlas300IDuo.md` | 勾选式进度 |
| `docs/基础环境及部署/华为Atlas300IDuo基础环境及应用部署方案.md` | 更细排障与背景 |
| `app/app-deploy/README-simple-deploy.md` | 应用配置精简说明 |
| `app/app-deploy/docker-ascend/README.md` | 昇腾 app 栈 |
| `vllm-deploy/README.md` | vLLM 多平台 overlay |
| `mineru-deploy/README.md` | MinerU CPU/GPU/Ascend |
| `rag_db-deploy/README.md` | EasySearch |

---

## 10. 常见问题（短答）

| 现象 | 处理 |
|------|------|
| `exec format error` | 镜像/驱动不是 aarch64；核对 §2 下载的是否为 ARM 包 |
| 容器内无 NPU | 查 Runtime、`ASCEND_RT_VISIBLE_DEVICES`、用户组、驱动卷 |
| pip 后 `torch_npu` 丢失 | 禁止用 CUDA `torch` 覆盖；Ascend Dockerfile 已校验 |
| app 连不上 vLLM | 查外部网络名、服务名，勿用 `127.0.0.1` |
| MinerU NPU 不稳 | 先 `docker-compose.cpu.yml` 保功能，再迭代 NPU |
| 只升驱动或只升镜像 | **禁止**；按 §2 矩阵整线升级 |
| 找不到下载包 | 回 §2.2：驱动/固件走昇腾社区页，Runtime 走 gitcode Releases，Docker 走 download.docker.com / GitHub compose |
