# 昇腾 GPU 应用栈（`docker-ascend/`）

本目录提供 **华为昇腾 Ascend（Atlas 300I Duo）** 环境下的 `models-app` 部署，与上级目录的 **CPU 栈**、**英伟达**（`docker-nvidia/`）、**沐曦**（`docker-mx/`）并列。

## 适用场景

- 宿主机为 **Atlas 300I Duo**，已安装 **NPU 驱动 26.1.1 / 固件 9.0.0.9.220** 与 **Ascend Docker Runtime 26.1.0**
- **默认**：嵌入/重排走独立 **MIS-TEI**（`mis-tei-deploy/`，`EMBEDDING_BACKEND=mis_tei`）
- **可选回退**：`EMBEDDING_BACKEND=local` + `EMBEDDING_DEVICE=npu:0`（进程内 ST，310P 上易遇 SDPA 问题，一般不推荐）
- 底座镜像默认：`quay.io/ascend/vllm-ascend:v0.23.0-310p`（与 vLLM / MinerU 同底座；建议 `.env` 写 `BASE_IMAGE`）

## 文件说明

| 文件 | 说明 |
|------|------|
| `Dockerfile-ascend` | `FROM` 昇腾底座 + 业务 requirements（保护 `torch_npu`） |
| `docker-compose-ascend.yml` | Redis、MinIO、`models-app`（external 含 `mis-tei-stack`） |

`.env` 使用 **`app/app-deploy/.env`**，compose 通过 `env_file: ../.env` 引用。

## 业务源码 mount（离线迭代）

- 默认宿主机：`/opt/deploy/models_app_framework/app` → 容器 `/workspace/app`
- 默认宿主机：`/opt/deploy/models_app_framework/configs` → 容器 `/workspace/configs`
- **有网机 build 镜像**（含 pip 与 COPY）；**离线机** sync 源码后 `docker compose restart models-app`
- 首次：在宿主机准备 `/opt/deploy/models_app_framework/{app,configs}` 并同步代码/配置后 `docker compose up -d`

## 四卡切分（与 `README-DEPLOY-ASCEND.md` §3 一致）

| 栈 | `ASCEND_RT_VISIBLE_DEVICES` |
|----|-----------------------------|
| vLLM | `0,1,2,3` |
| **mis-tei-embed / rerank** | **`4` / `5`**（见仓库根 `mis-tei-deploy/`） |
| **本栈 models-app** | **留空**（默认 mis_tei，勿与 TEI 争用 4/5） |
| MinerU | `6` |

应用 `.env` 关键项：`EMBEDDING_BACKEND=mis_tei`、`MIS_TEI_EMBED_BASE_URL=http://mis-tei-embed:8080`、`MIS_TEI_RERANK_BASE_URL=http://mis-tei-rerank:8080`。

## 启动

```bash
# 1) 先起 MIS-TEI（独立目录）
cd mis-tei-deploy && cp .env.example .env && docker compose --env-file .env up -d

# 2) 再起 app
cd app/app-deploy
cp .env.example .env   # 首次；确认 EMBEDDING_BACKEND=mis_tei 等

docker network create paddle-layout-stack 2>/dev/null || true
docker network create face-milvus-stack 2>/dev/null || true

docker compose -f docker-ascend/docker-compose-ascend.yml up -d --build
```

详细宿主机步骤见 `README-DEPLOY-ASCEND.md`、`docs/基础环境及部署/华为Atlas300IDuo基础环境及应用部署方案.md`。
