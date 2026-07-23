# 工作清单：华为 Atlas 300I Duo_96G（麒麟 V10 SP3）

> **适用分支**：`dev_djs`（地面沉降项目）  
> **硬件**：华为 Atlas 300I Duo_96G（双芯推理卡，合计约 96GB HBM）  
> **CPU**：双路 × 单颗 32 核（合计 64 核），主频 ≥2.0GHz；须落成 **ARM 或 C86（海光，x86_64）** 之一并全程同架构选型  
> **操作系统**：银河麒麟 Kylin V10 SP3（须与 CPU 架构匹配的安装介质）  
> **详细步骤与参数**：见同目录 [`华为Atlas300IDuo基础环境及应用部署方案.md`](./华为Atlas300IDuo基础环境及应用部署方案.md)

本文只列 **默认必须部署** 的环境与应用工作项（含仓库内待落地的代码/配置改造）。可选组件（GraphRAG/Neo4j、Paddle 版面侧车、小模型 GPU profile 等）不纳入本清单。

---

## 0. 范围确认（先勾选）

| ID | 工作项 | 状态 | 说明 |
|----|--------|------|------|
| S0-1 | 确认现场卡型号为 Atlas 300I Duo、HBM≈96GB | ☐ | `npu-smi info` / 资产清单 |
| S0-1a | **确认 CPU 路线落地值**：ARM（`aarch64`）或 C86/海光（`x86_64`）；记录型号、双路 32 核×2 | ☐ | `lscpu`、`uname -m`；驱动/镜像必须同架构 |
| S0-2 | 确认 OS 为 Kylin V10 SP3，且安装介质与 CPU 架构一致；记录内核版本 | ☐ | `cat /etc/os-release`、`uname -r` |
| S0-3 | 确认本项目默认必部：EasySearch + vLLM + MinerU + app（含 Redis/MinIO） | ☐ | 与业务方书面确认 |
| S0-4 | 确认不默认部署：paddleocr-layout、Neo4j、models-app-gpu | ☐ | 需要时另开任务 |

---

## 1. 宿主机基础环境（必须）

| ID | 工作项 | 状态 | 产出/验收 |
|----|--------|------|-----------|
| H1 | 系统与内核匹配性检查（驱动包与 Kylin V10 SP3 / 内核 / **CPU 架构**对应） | ☐ | 预检通过记录；`uname -m` 与驱动 run 包 arch 一致 |
| H1a | 核对制品：NPU 驱动、CANN、Docker 基础镜像均为 **同一架构**（禁止 ARM 机拉 amd64 昇腾镜像） | ☐ | 版本矩阵表已填架构列 |
| H2 | 安装编译/内核依赖（gcc、make、kernel-devel 等，按驱动包要求） | ☐ | 依赖齐备 |
| H3 | 安装 NPU **驱动 + 固件**（按华为文档区分首次/覆盖安装顺序） | ☐ | 重启后 `npu-smi info` 可见双芯 |
| H4 | 设备节点与权限（`/dev/davinci*`、`HwHiAiUser` 等） | ☐ | 业务用户可访问 NPU |
| H5 | 安装 Docker + Docker Compose V2（麒麟可用源或一键脚本） | ☐ | `docker version` / `docker compose version` |
| H6 | 安装 **Ascend Docker Runtime**（或现场等价的容器 NPU 注入方案）并注册 | ☐ | 容器内可见 davinci / `npu-smi` |
| H7 | （按所选镜像要求）安装/对齐 **CANN** 与镜像版本矩阵 | ☐ | 版本对照表落档 |
| H8 | 内核参数：`vm.max_map_count=262144`（EasySearch） | ☐ | `sysctl vm.max_map_count` |
| H9 | 规划并创建数据/模型目录（`/aidata/...`）与磁盘配额 | ☐ | 目录存在、权限正确 |
| H10 | 离线制品准备：驱动包、CANN、基础镜像、模型权重导入策略 | ☐ | 内网可达或已 load 镜像 |

---

## 2. 仓库侧适配改造（必须，当前缺口）

> 对照沐曦/英伟达已有形态补齐昇腾；**未完成则现场无法按方案一键部署**。

| ID | 工作项 | 状态 | 目录/文件（目标） |
|----|--------|------|-------------------|
| C1 | **完善** `vllm-deploy` Ascend overlay（默认 `BASE_IMAGE`、设备挂载、文档与 `.env` 示例） | ☐ | `vllm-deploy/docker/docker-compose.ascend.yml`、`.env.example`、`README.md` |
| C2 | **新增** `app-deploy` 昇腾栈（对齐 `docker-mx` / `docker-nvidia`） | ☐ | `app/app-deploy/docker-ascend/`（Dockerfile + compose + 简要 README） |
| C3 | **新增** `mineru-deploy` 昇腾 GPU 编排与镜像构建 | ☐ | `Dockerfile.gpu.ascend`、`docker-compose.gpu.ascend.yml`、`.env.example` |
| C4 | 选定并固化三栈 **华为官方/现场制品库镜像 tag**（vLLM / torch_npu / MinerU），且 tag 架构与现场 CPU 一致 | ☐ | 写入方案「版本矩阵」表（含 ARM 或 C86） |
| C5 | 双芯资源切分约定（vLLM TP=2 vs app 嵌入/重排 vs MinerU） | ☐ | `.env` 模板中的 `ASCEND_RT_VISIBLE_DEVICES` |
| C6 | 更新运维手册交叉引用（可选） | ☐ | `enterprise-level_transformation_docs/项目整体部署运维手册.md` |

---

## 3. 默认必部应用部署（必须）

按顺序执行；每步验收通过后再做下一步。

| ID | 工作项 | 状态 | 验收要点 |
|----|--------|------|----------|
| D1 | 部署 **EasySearch**（`rag_db-deploy`） | ☐ | `9200` 健康；admin 密码已设；应用侧账号一致 |
| D2 | 准备 LLM / 嵌入 / 重排 / MinerU 模型权重到约定宿主机路径 | ☐ | 路径与 compose 挂载一致 |
| D3 | 部署 **vLLM**（`VLLM_PLATFORM=ascend`） | ☐ | `/health`、`/v1/models`；双芯利用率符合预期 |
| D4 | 部署 **MinerU**（Ascend GPU 编排） | ☐ | `/health`；与 app 同网、同 IO 卷 |
| D5 | 部署 **app 栈**（昇腾 compose：Redis + MinIO + models-app） | ☐ | 对外端口可达；嵌入/重排走 NPU |
| D6 | 联通外部 Docker 网络（vllm / rag / mineru） | ☐ | 容器内用服务名互访成功 |
| D7 | 配置 `SERVICE_API_KEYS`、`LLM_*`、`RAG_ES_*`、`MINERU_*`、设备号 | ☐ | 业务接口鉴权与推理可用 |
| D8 | 端到端冒烟：健康检查 → 简单 chat → RAG 摄入/问答 →（可选）扫描 PDF | ☐ | 记录结果与耗时 |

---

## 4. 验收与交付（必须）

| ID | 工作项 | 状态 | 产出 |
|----|--------|------|------|
| A1 | 宿主机：`npu-smi info`、驱动/固件/CANN 版本表 | ☐ | 交付附件 |
| A2 | 容器：各服务健康检查与关键日志无致命错误 | ☐ | 检查记录 |
| A3 | 资源：双芯显存占用、是否与切分策略一致 | ☐ | 截图/命令输出 |
| A4 | 回滚要点：停栈顺序、镜像/配置备份位置 | ☐ | 写入运维备忘 |
| A5 | 将本清单全部 ☐ 勾完，并归档方案版本号与日期 | ☐ | 结项 |

---

## 5. 建议排期（可按现场调整）

| 阶段 | 内容 | 依赖 |
|------|------|------|
| P0 | H1–H10 宿主机基础环境 | 硬件到位、厂商软件包 |
| P1 | C1–C5 仓库昇腾适配合入 `dev_djs` | P0 版本矩阵可定 |
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
