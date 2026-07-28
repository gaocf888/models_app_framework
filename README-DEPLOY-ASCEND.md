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
确认 ARM + storcli 核对 VD0
  → 新建 VD1（RAID1+热备）并只对 VD1 分区
  → 按 §2 下载制品 → NPU 驱动/固件 → Docker/Compose → Ascend Docker Runtime
  → 拉底座镜像 → 落盘模型（VD1）→ EasySearch → vLLM → MinerU → app
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
| 存储（现场实测） | MegaRAID 9560-8i：VD0≈894G RAID1（系统**不重切**）；VD1≈1.75T RAID1+1 热备（业务数据） |

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

### 4.1 RAID 与分区（现场实测 · 详细操作）

> **原则**：保留 **VD0**（系统 RAID1）**不重切**；新建 **VD1**（2 盘 RAID1 + 1 热备）；业务数据（含 `/aidata/data`）全部在 VD1。  
> **危险**：下列命令会新建阵列并格式化 **数据盘**。执行前确认槽位仍为 UGood、**不会**改到 252:0/252:1（系统盘）。  
> **禁止**：对 `/dev/sda` 做 `mklabel`、删除 `sda3`、缩根。

#### 4.1.1 目标一览

| 项 | 值 |
|----|-----|
| 控制器 | MegaRAID **9560-8i**（`/c0`） |
| VD0（不动） | RAID1 ~894G；槽 **252:0 + 252:1** → `/dev/sda`（系统+Docker） |
| VD1（新建） | RAID1 ~1.746T；槽 **252:2 + 252:3** → 新建后常为 `/dev/sdb` |
| 热备 | 槽 **252:4** → Global Hot Spare（或专保 VD1 所在 DG） |
| 工具 | `storcli64`（已装到 `/opt/MegaRAID/storcli/storcli64`） |

**VD1 分区规划（约 1.75T 可用；下列 GiB 已按 ≈1700GiB 内可落地微调）**

| 分区名 | 挂载点 | 约大小 | 内容 |
|--------|--------|--------|------|
| MODELS | `/aidata/models` | 800G | LLM / 嵌入 / 重排 |
| MINERU | `/aidata/mineru` | 120G | MinerU |
| AIDATA | `/aidata/data` | 350G | EasySearch / Redis / 会话 |
| MINIO | `/aidata/data/minio_data` | 180G | MinIO |
| BACKUP | `/aidata/backup` | 100G | 备份 |
| DEPLOY | `/opt/deploy` | 剩余(~100G+) | 代码与离线包 |

> 若 `parted` 报超出磁盘末尾，从 BACKUP / MINERU 再压缩；**优先保 MODELS 与 AIDATA**。

---

#### 4.1.2 步骤 0：操作前核对（必做）

```bash
# 架构与 storcli
uname -m    # aarch64
which storcli64 || ls -l /opt/MegaRAID/storcli/storcli64
# 若无 PATH：export PATH=/opt/MegaRAID/storcli:$PATH

# 当前阵列与物理盘（确认 252:2/3/4 仍为 UGood）
storcli64 /c0 show
storcli64 /c0 /vall show
storcli64 /c0 /eall /sall show

# OS 盘：只应看到 sda（系统），尚无 sdb
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,MODEL
df -hT /
```

**通过标准**：

- VD LIST 仅 **1** 个 RAID1（~893.75G），State=Optl  
- PD：252:0/1 = Onln DG0；252:2/3/4 = **UGood**  
- `/` 在 `sda3`，业务尚未依赖即将格式化的盘  

若 252:2/3/4 状态不是 UGood，**停止**，先排障（Foreign 等）再继续。

---

#### 4.1.3 步骤 1：新建 VD1（RAID1）

> 仅使用空闲槽 **252:2** 与 **252:3**。名称 `aidata` 可改，勿动 252:0/1。

```bash
# 创建 RAID1，用满两盘容量；WriteBack + ReadAhead + Direct（与现场 VD0 策略接近，可按机房规范改 WT）
storcli64 /c0 add vd r1 name=aidata drives=252:2,252:3 WB RA Direct

# 若报错提示需 force（确认槽位无误后再加）：
# storcli64 /c0 add vd r1 name=aidata drives=252:2,252:3 WB RA Direct force
```

验收：

```bash
storcli64 /c0 /vall show
storcli64 /c0 show
# 期望：Virtual Drives = 2；新 VD 为 RAID1，Size≈1.746T，State 为 Optl（或初始化中）
# TOPOLOGY 中新增 DG（常见为 DG=1），两成员 Onln
```

若长时间非 Optl，可查看初始化进度：

```bash
storcli64 /c0 /vall show all
storcli64 /c0 show cc
# 可继续后续分区；生产更稳妥是等新 VD 为 Optl / Consist=Yes 再大量写数据
```

---

#### 4.1.4 步骤 2：设置热备（槽 252:4）

```bash
# 全局热备（推荐，可覆盖 VD0/VD1）
storcli64 /c0 /e252 /s4 add hotsparedrive

# 若只要守护新建数据组（DG 号以 /c0 show 的 TOPOLOGY 为准，常见为 1）：
# storcli64 /c0 /e252 /s4 add hotsparedrive dgs=1
```

验收：

```bash
storcli64 /c0 /eall /sall show
# 期望：252:4 状态为 GHS / DHS / Hotspare（不再是 UGood）
```

---

#### 4.1.5 步骤 3：让 OS 识别新磁盘

```bash
# 查看是否已出现第二块盘（常见 /dev/sdb）
lsblk -o NAME,SIZE,TYPE,MODEL
dmesg | tail -n 50

# 若尚未出现，触发 SCSI 扫描（host 号以现场为准）
ls /sys/class/scsi_host/
for h in /sys/class/scsi_host/host*; do
  echo "- - -" | sudo tee "$h/scan" >/dev/null
done
sleep 2
lsblk -o NAME,SIZE,TYPE,MODEL

# 仍没有则重启一次（最稳）
# sudo reboot
```

确认后固定变量（**按实际盘符修改**）：

```bash
# 用大小约 1.7T、无挂载的那块；切勿选中 sda
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
export DATA=/dev/sdb
lsblk "$DATA"
# 再确认一次不是系统盘：
findmnt / && lsblk /dev/sda
```

---

#### 4.1.6 步骤 4：对 VD1 分区（`parted`）

> 下列起止按「约 1.7T 可用」编排。超界则改小 BACKUP/MINERU。  
> **新建 VD 尚无分区表时**，`parted … print free` 可能报「无法辨识的磁盘卷标 / 分区表 unknown」——**属正常**，须先 `mklabel gpt`。

```bash
# 再次确认目标盘（确认 $DATA 不是 sda！）
echo "即将分区的磁盘: $DATA"
lsblk "$DATA"

# 1) 先建 GPT（空盘必做；消除 “无法辨识的磁盘卷标”）
sudo parted -s "$DATA" mklabel gpt

# 2) 再查看空闲并切分
sudo parted "$DATA" unit GiB print free

sudo parted -s "$DATA" mkpart MODELS xfs 1MiB 801GiB
sudo parted -s "$DATA" mkpart MINERU xfs 801GiB 921GiB
sudo parted -s "$DATA" mkpart AIDATA xfs 921GiB 1271GiB
sudo parted -s "$DATA" mkpart MINIO  xfs 1271GiB 1451GiB
sudo parted -s "$DATA" mkpart BACKUP xfs 1451GiB 1551GiB
sudo parted -s "$DATA" mkpart DEPLOY xfs 1551GiB 100%

sudo parted "$DATA" unit GiB print
lsblk "$DATA"
```

分区设备名（SCSI 盘常见为 `sdb1`…；若为 `nvme`/`mpath` 另论）：

```bash
# 按 lsblk 结果设置（示例）
export P_MODELS=${DATA}1
export P_MINERU=${DATA}2
export P_AIDATA=${DATA}3
export P_MINIO=${DATA}4
export P_BACKUP=${DATA}5
export P_DEPLOY=${DATA}6
lsblk "$P_MODELS" "$P_MINERU" "$P_AIDATA" "$P_MINIO" "$P_BACKUP" "$P_DEPLOY"
```

---

#### 4.1.7 步骤 5：格式化、挂载、**写入 fstab（必须）**

> **关键**：仅 `mount` 而不写 `/etc/fstab`，**重启后 `df` 会看不到业务盘**（`lsblk` 仍有 `sdb*` 分区、无 MOUNTPOINT）。现场曾因此误判为“盘丢了”。  
> `mkfs.xfs` **一次只能格式化一块设备**，勿把多个分区写在同一条命令里。

```bash
# 逐个格式化（不要写成 mkfs.xfs -f "$P_MODELS" "$P_MINERU" ...）
sudo mkfs.xfs -f "$P_MODELS"
sudo mkfs.xfs -f "$P_MINERU"
sudo mkfs.xfs -f "$P_AIDATA"
sudo mkfs.xfs -f "$P_MINIO"
sudo mkfs.xfs -f "$P_BACKUP"
sudo mkfs.xfs -f "$P_DEPLOY"

sudo mkdir -p /aidata/models /aidata/mineru /aidata/data \
  /aidata/data/minio_data /aidata/backup /opt/deploy

sudo mount "$P_MODELS" /aidata/models
sudo mount "$P_MINERU" /aidata/mineru
sudo mount "$P_AIDATA" /aidata/data
# minio 挂到 data 下的子路径：须先挂好 /aidata/data
sudo mkdir -p /aidata/data/minio_data
sudo mount "$P_MINIO"  /aidata/data/minio_data
sudo mount "$P_BACKUP" /aidata/backup
sudo mount "$P_DEPLOY" /opt/deploy

df -hT | grep -E 'aidata|deploy|Filesystem'
```

**写入 fstab（推荐：按当前挂载自动生成 UUID，避免手抄错误）**：

```bash
sudo cp -a /etc/fstab /etc/fstab.bak.$(date +%F-%H%M)

# 若曾手写过错误行，先删掉旧的 aidata/deploy 行再追加
sudo sed -i -E '/[[:space:]]\/aidata(\/|$)|[[:space:]]\/opt\/deploy/d' /etc/fstab

sudo bash -c '
for mp in /aidata/models /aidata/mineru /aidata/data /aidata/data/minio_data /aidata/backup /opt/deploy; do
  src=$(findmnt -n -o SOURCE --target "$mp")
  uuid=$(blkid -s UUID -o value "$src")
  test -n "$uuid" || { echo "缺少 UUID: $mp"; exit 1; }
  echo "UUID=$uuid  $mp  xfs  defaults  0 0"
done >> /etc/fstab
'

# 必须能 grep 到六行
grep -E 'aidata|/opt/deploy' /etc/fstab
sudo mount -a
echo "mount -a exit: $?"
findmnt /aidata/models /aidata/mineru /aidata/data /aidata/data/minio_data /aidata/backup /opt/deploy
```

> **挂载顺序**：`/aidata/data` 须先于 `/aidata/data/minio_data`。上面脚本顺序已保证；手写时 AIDATA 行也须在 MINIO 行之前。  
> **验收本步**：`grep aidata /etc/fstab` 非空；仅有系统盘三条 UUID（`/` `/boot` `/boot/efi`）而不含 aidata 则 **未完成**，重启必丢挂载。

---

#### 4.1.8 步骤 6：业务子目录与权限

```bash
sudo mkdir -p \
  /aidata/models/llm \
  /aidata/models/embeddings/Qwen3-Embedding-0.6B \
  /aidata/models/reranker/Qwen3-Reranker-0.6B \
  /aidata/mineru/models /aidata/mineru/io \
  /aidata/data/{redis_data,session_storage,easysearch} \
  /aidata/backup /opt/deploy/offline
sudo chown -R "$USER:$USER" /aidata /opt/deploy
```

---

#### 4.1.9 步骤 7：总验收（含 **reboot 后** 验证）

**当次挂载验收：**

```bash
storcli64 /c0 /vall show
storcli64 /c0 /eall /sall show
grep -E 'aidata|/opt/deploy' /etc/fstab    # 必须有 6 行
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT
df -hT | grep -E 'aidata|deploy|Filesystem'
```

| 检查项 | 期望 |
|--------|------|
| VD 数量 | 2；均为 RAID1 Optl |
| 252:4 | 热备（GHS/DHS，非 UGood） |
| `sda` | 仍为系统；分区未改 |
| `sdb`（或实际 DATA） | 六分区 **且 MOUNTPOINT 非空** |
| fstab | 含 `/aidata/...` 与 `/opt/deploy` 的 UUID 行 |
| `/` | 无大模型长期目录 |

**强制：reboot 后再验一次（否则不算完成）**

```bash
sudo reboot
# 登录后：
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT | grep -E 'sdb|NAME'
df -hT | grep -E 'aidata|deploy'
```

| 现象 | 含义 | 处理 |
|------|------|------|
| `lsblk` 有 `sdb1…6`（xfs），但 **无 MOUNTPOINT**，`df` 无 aidata | **fstab 未写或写错**；阵列/分区仍在 | 见下「排障」；补写 fstab 后 `mount -a` |
| `lsblk` 无 `sdb` | 控制器/驱动未认出 VD | `storcli64 /c0 /vall show`；SCSI rescan 或查驱动 |
| storcli 无第二 VD | 阵列被删/未创建 | 回到 §4.1.3 |

**排障：reboot 后 df 看不到业务盘（现场已验证根因）**

```bash
# 1) 区分「盘在但未挂」vs「盘丢了」
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT   # 有 sdb* + xfs、无挂载点 → 未挂载
grep -E 'aidata|deploy' /etc/fstab || echo "fstab 缺条目 ← 最常见原因"

# 2) 临时挂上
sudo mkdir -p /aidata/models /aidata/mineru /aidata/data \
  /aidata/data/minio_data /aidata/backup /opt/deploy
sudo mount /dev/sdb1 /aidata/models
sudo mount /dev/sdb2 /aidata/mineru
sudo mount /dev/sdb3 /aidata/data
sudo mkdir -p /aidata/data/minio_data
sudo mount /dev/sdb4 /aidata/data/minio_data
sudo mount /dev/sdb5 /aidata/backup
sudo mount /dev/sdb6 /opt/deploy

# 3) 按 §4.1.7 自动追加 UUID 到 fstab，再 mount -a / reboot 验证
```

**运维**：监控 `/` 使用率；可选后续将 Docker `data-root` 迁到 `/aidata/docker`（仍不必重切 `sda`）。

**回滚提示（仅数据盘）**：若 VD1 建错且尚未写业务数据，可删除虚拟盘后重来（**绝不**对 VD0 执行 delete）：

```bash
# 先 umount 所有 VD1 挂载并恢复 fstab，再：
# storcli64 /c0 /vX delete force    # X = 新 VD 编号，删除前务必确认不是系统 VD
# storcli64 /c0 /e252 /s4 delete hotsparedrive
```

### 4.2 NPU 驱动与固件

制品与下载见 **§2.2**（昇腾社区 Firmware-Drivers 页）。首次安装常见顺序（以官方 HDK 手册为准）：**先驱动，后固件**。

**前置（必须）**：驱动安装会检查 `HwHiAiUser`；若不存在会报 `ERR_NO:0x0091; HwHiAiUser not exists`。

```bash
# 创建昇腾驱动所需用户/组（已存在则跳过）
id HwHiAiUser 2>/dev/null || sudo useradd -m HwHiAiUser
# 部分环境需显式建组（useradd 通常会建同名组）
getent group HwHiAiUser >/dev/null || sudo groupadd HwHiAiUser

# 可选：将当前运维用户加入该组（装完驱动后也需，便于访问 /dev/davinci*）
sudo usermod -aG HwHiAiUser "$USER"
```

安装：

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
# 若仍有设备权限问题：确认已在 HwHiAiUser 组并重新登录
# id; groups
```

### 4.3 Docker 与 Compose

**架构**：离线包必须是 **aarch64**。  
**前置**：本现场 Docker 与 `/` 同在 VD0；建议 VD1 业务挂载已就绪后再大量 load 镜像。

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
| reboot 后 `df` 无 sdb/aidata，但 `lsblk` 仍有 `sdb*` | **fstab 未写业务盘**；按 §4.1.9 排障补 UUID 后 `mount -a`，再 reboot 验证 |
| `parted print free` 报无法辨识磁盘卷标 | 新 VD 尚无 GPT；先 `mklabel gpt`（§4.1.6） |
| `mkfs.xfs` 报 `extra arguments` | 一次只格式化一块设备（§4.1.7） |
| 驱动安装报 `HwHiAiUser not exists` / `0x0091` | 先 `useradd -m HwHiAiUser`（§4.2），再重跑 `.run` |
