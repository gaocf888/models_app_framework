# compiled-common

吉泰 compiled 镜像共用的 **Nuitka** 入口。`docker-mx-compiled/` 与 `docker-nvidia-compiled/` 的 Dockerfile 在 builder 阶段 `COPY` 本目录。

- `compile_app.sh`：容器内入口，解释器由环境变量 `PYTHON` 指定。
- `compile_app.py`：按文件 `nuitka --module --nofollow-imports`，删除已成功编译的 `.py`；失败项写入 `app/.compile_whitelist.txt`。不按文件大小跳过。`__init__.py` 不交给 Nuitka（它要求编包目录，会破坏按文件 `.so`），作为包标记明文保留。`app/main.py` 同样保留（uvicorn 入口）；`app/__init__.py` 内含 Python 3.12 × Nuitka loader 兼容补丁。
- `compile_keep_py.txt`：预置跳过编译的 `app/` 相对路径（实验室编不过时再填）。

Nuitka 比 Cython 慢（单个大文件可能数十分钟）。单文件超时默认 1800 秒，可用 `NUITKA_FILE_TIMEOUT` 覆盖。全量构建可能数小时。

构建上下文仍是仓库根；排除 `app-deploy` / `train` / `test_scripts` / `manage_scripts` 由脚本在 builder 里删除。
