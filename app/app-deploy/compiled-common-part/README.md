# compiled-common-part（只编 `app/services` + 全量挂载部署）

与 `compiled-common/docker-mx-compiled/` / `compiled-common/docker-nvidia-compiled/`（整包编进镜像、不挂 `app/`）**并存、不要混用**。

本目录：在**与运行相同的 Docker 基座解释器**里只编译 `app/services`，交付树中该目录以 `.so` 为主；部署时 **`app/` 与 `configs/` 全部 bind-mount**，镜像不 `COPY` 业务源码。**运行镜像在现场 `up --build` 时再打**；编译镜像只用于实验室编 `.so`，部署不用。

不要在宿主机系统 Python 或 Windows 上直接 Nuitka。沐曦必须是 conda 3.12（与运行 Dockerfile 同一 Metax 基座）；英伟达必须是 CPython 3.11（与运行 Dockerfile 同一 `python:3.11-slim`）。两套 `.so` 不能互换。

## 1. 交付前编译

在 **Linux 实验室**、对应卡型机器上执行（编完再打包给客户，现场不再编译）：

```bash
cd app/app-deploy/compiled-common-part
chmod +x compile.sh
sed -i 's/\r$//' compile.sh    # 若从 Windows 拷出、报 /bin/sh^M 则先执行
./compile.sh mx        # 或 nvidia，须与客户现场卡型一致
```

脚本**只**构建 `models-app-*-part-compile`（gcc + Nuitka），把仓库挂到 `/workspace` 编 `app/services`，成功则删除对应 `.py`。**不打** `models-app-*-part` 运行镜像。

看结果：`app/services/.compile_report.txt`、`.compile_whitelist.txt`。`__init__.py` 会保留。`*_bak_*` 不编，交付前请删备份 `.py`。

## 2. 交付说明（编译后的源码 + 部署文件）

交给客户的是「已编译的工程树」，不是再让客户跑 `compile.sh`。建议包含：

| 内容 | 说明 |
|------|------|
| 仓库源码（`app/`、`configs/` 等） | `app/services/` 中业务 `.py` 已删除，仅 `.so` + `__init__.py`；其余包仍为源码 |
| `app/app-deploy/` | `.env.example`、本目录 `compiled-common-part/docker-mx/` 或 `docker-nvidia/`（运行 Dockerfile + compose） |
| **不要**交编译镜像 | `*-part-compile` 仅实验室用；现场不 `run` compile 容器 |

卡型必须与编译命令一致：`./compile.sh nvidia` 的包只能配英伟达现场；沐曦须用 `./compile.sh mx` 另编一套。

客户若沿用现网 `docker-nvidia/` / `docker-mx/`（`COPY app` 进镜像），也可以用这份已编 `app/` 做 `--build`，但那是打进镜像，不是本目录的挂载方式。本 README 的现场启动按 **挂载** 来写。

## 3. 现场启动

外部依赖与现网相同（EasySearch、vLLM、可选 MinerU）。**此时才打运行镜像**（基础镜像 + pip，不含业务 COPY）。

```bash
cd app/app-deploy
cp .env.example .env         # 首次
# 按手册改 LLM / RAG / DB / 网络名
cp .env compiled-common-part/docker-mx/.env         # 或 docker-nvidia
cd compiled-common-part/docker-mx                    # 或 docker-nvidia
docker compose --env-file .env -f docker-compose-mx.yml up -d --build
# 英伟达：cd compiled-common-part/docker-nvidia
# docker compose --env-file .env -f docker-compose-nvidia.yml up -d --build
```

挂载默认：`APP_HOST_PATH` → `/workspace/app`，`CONFIGS_HOST_PATH` → `/workspace/configs`。

## 4. 目录

| 路径 | 作用 |
|------|------|
| `compile_services.py` / `compile.sh` | 交付前只编 `app/services` |
| `compile_keep_py.txt` | 可选保留明文的 services 相对路径 |
| `docker-mx/` `docker-nvidia/` | 运行 Dockerfile（无 COPY app/configs）+ 现场 compose + `docker-compose.compile.yml` |

镜像名：`models-app-mx-part` / `models-app-nvidia-part`（**部署时**构建），`*-part-compile`（**仅编译**，部署不用）。
