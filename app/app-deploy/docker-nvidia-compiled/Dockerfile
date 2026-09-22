# =============================================================================
# 吉泰英伟达：业务 Python Cython 编译进镜像（多阶段，最终层无源码 COPY）
# =============================================================================
# 构建上下文必须是仓库根 models_app_framework。
# 现网 docker-nvidia/Dockerfile-nvidia 不改；本文件仅给 docker-nvidia-compiled 使用。
#
# builder：与现网相同的 CPython 3.11 + cu121 PyTorch 上编 .so
# runtime：同一基座 + 同一 pip 依赖，只 COPY 编译后的 /workspace/app
# configs/ 不打进最终镜像，由 compose bind-mount
# =============================================================================

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace \
    PIP_DEFAULT_TIMEOUT=300 \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libsm6 libxext6 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST

RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --timeout=300 --retries=15 \
        torch torchvision \
        --index-url https://download.pytorch.org/whl/cu121

COPY requirements-大模型应用.txt /workspace/requirements-app.txt
RUN pip install --no-cache-dir --timeout=300 --retries=15 \
        ${PIP_INDEX_URL:+-i} ${PIP_INDEX_URL:+$PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host} ${PIP_TRUSTED_HOST:+$PIP_TRUSTED_HOST} \
        -r /workspace/requirements-app.txt \
    && pip install --no-cache-dir --timeout=300 --retries=15 \
        torch torchvision \
        --index-url https://download.pytorch.org/whl/cu121

# ---------------------------------------------------------------------------
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
RUN pip install --no-cache-dir --timeout=300 --retries=15 \
        ${PIP_INDEX_URL:+-i} ${PIP_INDEX_URL:+$PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host} ${PIP_TRUSTED_HOST:+$PIP_TRUSTED_HOST} \
        cython

COPY app /workspace/app
COPY app/app-deploy/compiled-common /tmp/compiled-common
RUN set -e; \
    chmod +x /tmp/compiled-common/compile_app.sh; \
    PYTHON=python /tmp/compiled-common/compile_app.sh /workspace /workspace/app; \
    rm -rf /tmp/compiled-common /workspace/.cython_build; \
    find /workspace -maxdepth 1 -name '.cython_setup_*.py' -delete || true; \
    find /workspace/app -type d -name '__pycache__' -prune -exec rm -rf {} + || true; \
    test -f /workspace/app/.compile_report.txt

# ---------------------------------------------------------------------------
FROM base AS runtime

COPY --from=builder /workspace/app /workspace/app
RUN mkdir -p /workspace/configs /workspace/logs

EXPOSE 8083

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
