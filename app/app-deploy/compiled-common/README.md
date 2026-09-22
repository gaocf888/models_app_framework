# compiled-common

吉泰 compiled 镜像共用的 Cython 入口。`docker-mx-compiled/` 与 `docker-nvidia-compiled/` 的 Dockerfile 在 builder 阶段 `COPY` 本目录。

- `compile_app.sh`：容器内入口，解释器由环境变量 `PYTHON` 指定。
- `compile_app.py`：按文件 Cython 化，删除已成功编译的 `.py`，失败项写入 `app/.compile_whitelist.txt`。
- `compile_keep_py.txt`：预置跳过编译的 `app/` 相对路径（实验室编不过时再填）。

构建上下文仍是仓库根；Docker 只读取上下文根的 `.dockerignore`，因此排除 `app-deploy` / `train` / `test_scripts` / `manage_scripts` 由脚本在 builder 里 `rm` 完成，避免改现网 `docker-mx` / `docker-nvidia` 的 `COPY app` 行为。
