# 结项移交文档入口（dev_zajt）

本目录是 **部署运维** 与 **源码移交** 的唯一入口。接方先读本文，再按角色打开对应主文。

> 详细变量以各组件 `.env.example` 为准；本文档只写结项范围内「能起服务、能验收五条业务线」所需内容。

---

## 1. 先读哪一篇

| 角色 | 文档 | 用途 |
|------|------|------|
| 实施 / 值班 / 运维 | [整体部署运维手册.md](./整体部署运维手册.md) | 装什么、怎么起、改哪些环境变量、五条业务怎么验收、备份与排障 |
| 二次开发 / 改 Prompt / 改接口 | [源码移交说明.md](./源码移交说明.md) | 交哪些目录、业务线对应代码、改哪里、哪些能力未上线勿动 |
| 值班快速止血 | [online-services-oncall-runbook.md](./online-services-oncall-runbook.md) | 5 分钟健康检查与客服排障 |
| 备份目录对照 | [项目容器本地挂载和备份说明.md](./项目容器本地挂载和备份说明.md) | 宿主机 bind mount 清单 |
| 已踩过的坑 | [问题处理清单.md](./问题处理清单.md) | Python 环境错位、端口占用、EasySearch 内核参数等 |

`chatbot-deploy.md` 为智能客服专项附录；**启动命令与端口以《整体部署运维手册》为准**（该附录中部分 compose 文件名可能过时）。

---

## 2. 结项交付范围（源码包）

**交：**

- `app/`（含 `app/app-deploy/`）
- `configs/`
- `vllm-deploy/`
- `rag_db-deploy/`
- `mineru-deploy/`
- 仓库根目录 `requirements-大模型应用.txt`、`requirements-人脸识别-CPU.txt`（镜像构建 `COPY` 依赖）
- `deploy-docs/`（本目录）

**不交：** `paddleocr-layout-deploy/`、`monitoring-deploy/`、`face_db-deploy/`、`graphrag_db-deploy/`、`benchmarks/`、`docs/`、`framework-guide/`、`enterprise-level_transformation_docs/`、`memory-bank/`、`tests/`、`mis-tei-deploy/` 等未上线或设计稿目录。

**不在 Git、必须由现网一并交代的资产：** 大模型权重、嵌入/重排模型、EasySearch 数据盘、MinIO/Redis 挂载、业务 MySQL。

---

## 3. 结项业务线（现网）

| 业务线 | 调用方名称 | 主接口 |
|--------|------------|--------|
| 智能客服 | AI 问答 | `POST /chatbot/chat/stream` |
| 检修报告结构化解析 | 检修提取 | `POST /inspection-extract/upload` + `/run` 或 `/run/async` |
| 超温分析 | 综合分析 | `POST /analysis/run-with-nl2sql-stream`（`analysis_type=overheat_guidance`） |
| 缺陷识别 | 看图诊断 | `POST /analysis/img-diag/upload` + `/run-img-diag-stream`（`img_diag_subtype=defect_ident`） |
| 泄爆分析 | 看图诊断 | 同上，`img_diag_subtype=leakage_burst` |

五条业务的验收命令、依赖组件、常见故障见《整体部署运维手册》第 7 节；代码入口见《源码移交说明》第 3 节。
