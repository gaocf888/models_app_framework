# 吉泰沐曦 compiled（`docker-mx-compiled/`）

业务 Python 编成 `.so` 打进 `models-app-mx-compiled`。现网 `docker-mx/` 不改。

## 启动

前置：EasySearch、vLLM（mthreads overlay）、可选 MinerU 已起，且 external 网络存在。

```bash
cd app/app-deploy
cp .env.example .env          # 首次，按整体部署运维手册改
cp .env docker-mx-compiled/.env
cd docker-mx-compiled
docker compose --env-file .env -f docker-compose-mx-compiled.yml up -d --build
```

`configs/` 默认挂 `../../../configs` → `/workspace/configs`。**不要**把宿主机 `app/` 挂到 `/workspace/app`。

验收通过后删掉宿主机 `app/` 下除 `app-deploy` 外的代码，并 `docker builder prune`。之后只 `up -d`，禁止 `--build`。

## 构建产物

- 镜像 tag：`models-app-mx-compiled:latest`
- 容器内编译报告：`/workspace/app/.compile_report.txt`、`.compile_whitelist.txt`（Nuitka 失败而保留的 `.py`）
- 编译器：Nuitka 分档并行（小文件多进程、大文件少进程高 `--jobs`）；&lt;2KB 默认留明文
- 重复构建：Dockerfile 使用 BuildKit cache mount（Nuitka cache + ccache）
- 编译脚本：`../compiled-common/`

解释器必须是 `/opt/conda/bin/python`（与现网 Dockerfile-mx 一致）。
