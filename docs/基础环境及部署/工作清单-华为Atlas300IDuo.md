# 工作清单：华为 Atlas 300I Duo_96G（麒麟 V10 SP3）

> **适用分支**：`dev_djs`（地面沉降项目）  
> **硬件**：华为 Atlas 300I Duo_96G **×4**（每卡 Duo 双芯约 96GB；整机约 **8 NPU / ~384GB**）  
> **CPU**：双路 × 单颗 32 核（合计 64 核），主频 ≥2.0GHz；**已锁定 ARM（`aarch64`）**  
> **操作系统**：银河麒麟 Kylin V10 SP3（**ARM** 安装介质）  
> **统一底座镜像**：`quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`（vLLM / app / MinerU 共用；内含 **CANN 8.2.RC1**）  
> **NPU 驱动 / 固件**：`Ascend-hdk-310p-npu-driver_25.2.0_linux-aarch64.run` + `Ascend-hdk-310p-npu-firmware_7.7.0.6.236.run`  
> **Ascend Docker Runtime**：`Ascend-docker-runtime_7.1.RC1_linux-aarch64.run`（[下载](https://gitcode.com/Ascend/mind-cluster/releases/v7.1.RC1)）  
> **CANN**：宿主机 **不单独安装**；由官方 AI 镜像提供  
> **详细步骤与参数**：见同目录 [`华为Atlas300IDuo基础环境及应用部署方案.md`](./华为Atlas300IDuo基础环境及应用部署方案.md)

本文只列 **默认必须部署** 的环境与应用工作项（含仓库内待落地的代码/配置改造）。可选组件（GraphRAG/Neo4j、Paddle 版面侧车、小模型 GPU profile 等）不纳入本清单。

---

## 0. 范围确认（先勾选）

| ID | 工作项 | 状态 | 说明 |
|----|--------|------|------|
| S0-1 | 确认现场为 Atlas 300I Duo_96G **×4**，`npu-smi` 约 **8** 逻辑设备，记录卡号↔设备号 | ☐ | `npu-smi info` / 资产清单 |
| S0-1a | **确认 CPU 为 ARM**：`uname -m` = `aarch64`；记录型号、双路 32 核×2 | ☐ | `lscpu`、`uname -m`；禁止 amd64 驱动/镜像 |
| S0-2 | 确认 OS 为 Kylin V10 SP3（ARM 介质）；记录内核版本 | ☐ | `cat /etc/os-release`、`uname -r` |
| S0-3 | 确认本项目默认必部：EasySearch + vLLM + MinerU + app（含 Redis/MinIO） | ☐ | 与业务方书面确认 |
| S0-4 | 确认不默认部署：paddleocr-layout、Neo4j、models-app-gpu | ☐ | 需要时另开任务 |

---

## 1. 宿主机基础环境（必须）

| ID | 工作项 | 状态 | 产出/验收 |
|----|--------|------|-----------|
| H0 | 用 **storcli** 核对 MegaRAID：**VD0** RAID1 Optl（系统 ~894G）；物理盘与方案 §4.0 一致 | ☐ | `storcli64 /c0 show`；见方案现场实测表 |
| H0a | 确认引导分区：`/boot/efi`≈1G、`/boot`≈1G；**VD0 不重切**（系统+Docker 留在 `/`） | ☐ | `df -h /boot /boot/efi /`；勿缩根 |
| H0b | **新建 VD1**：空闲盘 2×~1.75T 做 RAID1 + 1 块热备；OS 见第二块盘（常为 `sdb`） | ☐ | `storcli64 /c0 /vall show`；两 VD 均 Optl + Hot Spare |
| H0c | 仅对 **VD1** 按方案 §4.0 分区并 fstab（含 `/aidata/data`、models、mineru、minio、backup、deploy） | ☐ | `df -h` 目标挂载均在 VD1；大文件不在 `/` |
| H1 | 系统与内核匹配性检查（HDK 25.2.0 支持 Kylin V10 SP3 / 本机内核 / **aarch64**） | ☐ | 预检通过；`uname -m`=`aarch64` |
| H1a | 核对制品均为 **aarch64/arm64**（驱动 run、Docker 静态包、`vllm-ascend:…-310p`） | ☐ | 禁止 ARM 机拉 amd64 昇腾镜像 |
| H1b | 按固化包名准备驱动/固件（对齐 CANN 8.2.RC1 / 镜像；勿装「最新」） | ☐ | 驱动 `…driver_25.2.0_linux-aarch64.run`；固件 `…7.7.0.6.236.run`；[下载页](https://www.hiascend.com/hardware/firmware-drivers/community?product=2&model=17&cann=8.2.RC1&driver=Ascend+HDK+25.2.0) |
| H2 | 安装编译/内核依赖（gcc、make、kernel-devel 等，按驱动包要求） | ☐ | 依赖齐备 |
| H3 | 安装 NPU **驱动 25.2.0 + 固件 7.7.0.6.236**（按华为文档区分首次/覆盖安装顺序） | ☐ | 重启后 `npu-smi info` 可见 **4 卡 / 约 8 设备** |
| H4 | 设备节点与权限（`/dev/davinci*`、`HwHiAiUser` 等） | ☐ | 业务用户可访问 NPU |
| H5 | 安装 Docker + Compose（§4.4：**在线一键** 或 **离线** `docker-20.10.24.tgz` + `docker-compose-linux-aarch64`） | ☐ | `docker version`；`docker compose` 或 `docker-compose`；Root Dir=`/var/lib/docker`（本现场与 `/` 同盘，须监控根分区用量） |
| H6 | 安装 **Ascend Docker Runtime 7.1.RC1**（`Ascend-docker-runtime_7.1.RC1_linux-aarch64.run`；见方案 §4.5） | ☐ | `docker info` → `Default Runtime: ascend`；`docker run --runtime=ascend … npu-smi info` 成功；[下载](https://gitcode.com/Ascend/mind-cluster/releases/v7.1.RC1) |
| H7 | **不在宿主机安装 CANN**；拉取并核对镜像内 CANN **8.2.RC1** | ☐ | `docker pull quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`；容器内版本标识落档 |
| H8 | 内核参数：`vm.max_map_count=262144`（EasySearch） | ☐ | `sysctl vm.max_map_count` |
| H9 | 在 **VD1 已挂载** 路径上创建 `/aidata/...`、`/opt/deploy` 子目录 | ☐ | 目录存在、权限正确；模型/ES/MinIO **不在** `/` |
| H10 | 离线制品：驱动+固件、**Ascend-docker-runtime_7.1.RC1_linux-aarch64.run**、**docker-20.10.24.tgz + docker-compose-linux-aarch64**、底座镜像 `v0.10.0rc1-310p`、模型权重 | ☐ | 内网可达或已拷贝/`docker load` |

---

## 2. 仓库侧适配改造（必须，当前缺口）

> 对照沐曦/英伟达已有形态补齐昇腾；**未完成则现场无法按方案一键部署**。

| ID | 工作项 | 状态 | 目录/文件（目标） |
|----|--------|------|-------------------|
| C1 | **完善** `vllm-deploy` Ascend overlay（默认 `BASE_IMAGE=…v0.10.0rc1-310p`、设备挂载、文档与 `.env` 示例） | ☑ | `Dockerfile-ascend`、`docker-compose.ascend.yml`、`.env.example`、`README.md` |
| C2 | **新增** `app-deploy` 昇腾栈（`FROM` 同上底座 + 业务依赖；对齐 `docker-mx` / `docker-nvidia`） | ☑ | `app/app-deploy/docker-ascend/` |
| C3 | **新增** `mineru-deploy` 昇腾 GPU 编排与镜像构建（底座同上） | ☑ | `Dockerfile.gpu.ascend`、`docker-compose.gpu.ascend.yml`、`.env.example` |
| C4 | 三栈默认镜像 tag 固化为 **`quay.io/ascend/vllm-ascend:v0.10.0rc1-310p`**（ARM；与驱动/固件/CANN 配套） | ☑ | 与方案 §3 一致；各 `.env.example` 已对齐 |
| C5 | **四卡**资源切分（§5：vLLM `0,1,2,3` / app `4,5` / MinerU `6`） | ☑ | 各栈 `.env.example` / compose 默认值 |
| C6 | 更新运维手册交叉引用（可选） | ☐ | `enterprise-level_transformation_docs/项目整体部署运维手册.md` |

---

## 3. 默认必部应用部署（必须）

按顺序执行；每步验收通过后再做下一步。

| ID | 工作项 | 状态 | 验收要点 |
|----|--------|------|----------|
| D1 | 部署 **EasySearch**（`rag_db-deploy`） | ☐ | `9200` 健康；admin 密码已设；应用侧账号一致 |
| D2 | 准备 LLM / 嵌入 / 重排 / MinerU 模型权重到约定宿主机路径 | ☐ | 路径与 compose / `mis-tei-deploy` 挂载一致（BGE 或现场选用模型） |
| D3 | 部署 **vLLM**（`VLLM_PLATFORM=ascend`；底座以现场文档为准） | ☐ | `/health`、`/v1/models`；占用设备 `0,1,2,3` |
| D3b | 部署 **MIS-TEI**（`mis-tei-deploy/`：embed + rerank） | ☐ | `/health`、`/embed`、`/rerank`；占设备 `4` / `5`；网络 `mis-tei-stack` |
| D4 | 部署 **MinerU**（Ascend；底座同上 + NPU 业务层） | ☐ | `/health`；与 app 同网、同 IO 卷；设备 `6`，与 vLLM/mis-tei **不重叠** |
| D5 | 部署 **app 栈**（昇腾 compose：Redis + MinIO + models-app） | ☐ | 对外端口可达；默认 `EMBEDDING_BACKEND=mis_tei` 调 MIS-TEI |
| D6 | 联通外部 Docker 网络（vllm / rag / mineru / **mis-tei**） | ☐ | 容器内用服务名互访成功 |
| D7 | 配置 `SERVICE_API_KEYS`、`LLM_*`、`RAG_ES_*`、`MINERU_*`、设备号 | ☐ | 业务接口鉴权与推理可用 |
| D8 | 端到端冒烟：健康检查 → 简单 chat → RAG 摄入/问答 →（可选）扫描 PDF | ☐ | 记录结果与耗时 |

---

## 4. 验收与交付（必须）

| ID | 工作项 | 状态 | 产出 |
|----|--------|------|------|
| A1 | 宿主机：`npu-smi`、驱动 **25.2.0**、固件 **7.7.0.6.236**、Runtime **7.1.RC1**（`Default Runtime: ascend`）；镜像内 CANN **8.2.RC1** | ☐ | 交付附件 |
| A2 | 容器：各服务健康检查与关键日志无致命错误 | ☐ | 检查记录 |
| A3 | 资源：四卡占用与 §5 切分一致（三栈设备号无重叠） | ☐ | 截图/命令输出 |
| A4 | 回滚要点：停栈顺序、镜像/配置备份位置 | ☐ | 写入运维备忘 |
| A5 | 将本清单全部 ☐ 勾完，并归档方案版本号与日期 | ☐ | 结项 |

---

## 5. 建议排期（可按现场调整）

| 阶段 | 内容 | 依赖 |
|------|------|------|
| P0 | H0–H10 宿主机（storcli 核对 VD0、新建 VD1+分区、驱动/固件、Docker、拉底座镜像） | 硬件到位、厂商软件包；StorCLI aarch64 |
| P1 | C1–C5 仓库昇腾适配合入 `dev_djs` | P0；镜像 tag 已固化 |
| P2 | D1–D4 数据面 + 推理 + MinerU | P0+P1 |
| P3 | D5–D8 应用联调与冒烟 | P2 |
| P4 | A1–A5 验收交付 | P3 |

---

## 明确不在默认范围（勿混入本清单）

| 组件 | 原因 |
|------|------|
| `paddleocr-layout-deploy` | 检修 V0 可选；地降所默认可不部署 |
| `graphrag_db-deploy` / Neo4j | GraphRAG 可选 |
| `models-app-gpu`（small-model profile） | 小模型/视频通道可选 |
| `face_db-deploy` | 人脸库场景可选 |
| 离线 YOLO 训练工程 | 非在线推理默认路径 |
| 宿主机单独安装 CANN / ascend-toolkit | 本方案由官方 AI 镜像提供 CANN 8.2.RC1 |
