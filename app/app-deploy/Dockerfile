# =============================================================================
# 默认在线应用镜像：大模型应用依赖（无 CUDA PyTorch / 无 Ultralytics）
# =============================================================================
# 构建上下文（context）：必须是仓库根目录 models_app_framework
#   docker compose 中已写 context: ../..  relative to app/app-deploy
#
# 包含：
#   - requirements-大模型应用.txt（FastAPI、RAG、ES 客户端、sentence-transformers 等）
#   - app/ 源码
#   - configs/（prompt_registry、small_model 等 YAML 运行时相对工作目录 /workspace）
#
# 不包含：小模型 GPU 推理依赖 → 使用 Dockerfile.small-model-gpu + profile small-model-gpu
# =============================================================================

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace \
    PIP_DEFAULT_TIMEOUT=300

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 构建阶段若出现 json.decoder.JSONDecodeError（解析 PyPI 索引 JSON 失败），多为响应被截断/超时；
# 已加大 pip 超时与重试。仍不稳定时可指定镜像，例如：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#     --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

COPY requirements-大模型应用.txt /workspace/requirements-app.txt
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --timeout=300 --retries=15 \
        ${PIP_INDEX_URL:+-i} ${PIP_INDEX_URL:+$PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host} ${PIP_TRUSTED_HOST:+$PIP_TRUSTED_HOST} \
        -r /workspace/requirements-app.txt

COPY app /workspace/app
COPY configs /workspace/configs

EXPOSE 8083

# 生产 worker 数由 compose command 传入；此仅为镜像默认入口
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
