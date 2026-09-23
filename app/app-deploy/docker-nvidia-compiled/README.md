# 吉泰英伟达 compiled（`docker-nvidia-compiled/`）

业务 Python 编成 `.so` 打进 `models-app-nvidia-compiled`。现网 `docker-nvidia/` 不改。

## 启动

前置：NVIDIA 驱动 + Container Toolkit；EasySearch、vLLM（nvidia overlay）、可选 MinerU 已起。

```bash
cd app/app-deploy
cp .env.example .env
cp .env docker-nvidia-compiled/.env
cd docker-nvidia-compiled
docker compose --env-file .env -f docker-compose-nvidia-compiled.yml up -d --build
```

`configs/` 默认挂 `../../../configs` → `/workspace/configs`。**不要**把宿主机 `app/` 挂到 `/workspace/app`。

验收通过后删掉宿主机 `app/` 下除 `app-deploy` 外的代码，并 `docker builder prune`。之后只 `up -d`，禁止 `--build`。

## 构建产物

- 镜像 tag：`models-app-nvidia-compiled:latest`
- 容器内编译报告：`/workspace/app/.compile_report.txt`、`.compile_whitelist.txt`（Nuitka 失败而保留的 `.py`）
- 编译器：Nuitka（`--module --nofollow-imports`），不按文件大小跳过；全量构建可能数小时
- 编译脚本：`../compiled-common/`

`.so` 必须在本机 CPython 3.11 + cu121 镜像内编译，不能与沐曦 compiled 镜像互换。
