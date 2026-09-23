# compiled-common

吉泰 compiled 镜像共用的 **Nuitka** 入口。`docker-mx-compiled/` 与 `docker-nvidia-compiled/` 的 Dockerfile 在 builder 阶段 `COPY` 本目录。

- `compile_app.sh`：容器内入口，解释器由环境变量 `PYTHON` 指定。
- `compile_app.py`：按文件 `nuitka --module --nofollow-imports`，分档并行；删除已成功编译的 `.py`。失败项写入 `app/.compile_whitelist.txt`。
- `__init__.py` 不交给 Nuitka（包标记）。`app/main.py` 保留（uvicorn 入口）。`app/__init__.py` 含 Python 3.12 × Nuitka loader 补丁。
- `compile_keep_py.txt`：预置跳过编译的 `app/` 相对路径（实验室编不过时再填）。

**并行分档**

| 档位 | 体积 | 默认并发 | 单文件 `--jobs` |
|------|------|----------|-----------------|
| small | &lt; 20KB | `min(ncpu, 8)` | 1 |
| medium | 20–100KB | `min(4, ncpu/2)` | 2 |
| large | ≥ 100KB | 1（≥24 核时 2） | `min(8, ncpu)` |

环境变量：`COMPILE_WORKERS_SMALL` / `COMPILE_JOBS_SMALL`（以及 `MEDIUM`、`LARGE`）。大文件并行太多容易 OOM，先加 workers 小档。

**其它**

- `COMPILE_MIN_BYTES` 默认 `2048`：更小的模块留明文（薄工具/常量）。设为 `0` 则全部尝试编译。
- 构建时排除：`app-deploy` / `train` / `test_scripts` / `manage_scripts` / `data_query_agent` / `observability`。`small_models` 仍编（`main.py` 挂了 `/small-model`、`/face`）。
- Dockerfile 对 `/var/cache/nuitka` 与 `/root/.ccache` 使用 BuildKit cache mount；重复 `--build` 可复用。Compose V2 默认 BuildKit，**不要**加 `# syntax=docker/dockerfile:1`（会去拉 Docker Hub 的 frontend 镜像，内网/国内镜像源常失败）。
- 单文件超时默认 1800 秒，`NUITKA_FILE_TIMEOUT` 可覆盖。
