# =============================================================================
# 吉泰沐曦：业务 Python Nuitka 编译进镜像（多阶段，最终层无源码 COPY）
# =============================================================================
# 构建上下文必须是仓库根 models_app_framework。
# 现网 docker-mx/Dockerfile-mx 不改；本文件仅给 docker-mx-compiled 使用。
#
# builder：与现网相同的 Metax conda Python 3.12 上编 .so
# runtime：同一基座 + 同一 pip 依赖，只 COPY 编译后的 /workspace/app
# configs/ 不打进最终镜像，由 compose bind-mount
# =============================================================================

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

FROM cr.metax-tech.com/public-ai-release/maca/vllm-metax:0.17.0-maca.ai3.5.3.307-torch2.8-py312-kylinv11-amd64 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace \
    PIP_DEFAULT_TIMEOUT=300 \
    PATH=/opt/conda/bin:${PATH}

WORKDIR /workspace

RUN yum update -y && \
    yum install -y --setopt=tsflags=nodocs \
    libxcb \
    mesa-libGL \
    libgomp \
    && yum clean all && \
    rm -rf /var/cache/yum

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST

COPY requirements-大模型应用.txt /workspace/requirements-app.txt
RUN /opt/conda/bin/pip install --upgrade pip setuptools wheel \
    && /opt/conda/bin/pip install --no-cache-dir --timeout=300 --retries=15 \
        ${PIP_INDEX_URL:+-i} ${PIP_INDEX_URL:+$PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host} ${PIP_TRUSTED_HOST:+$PIP_TRUSTED_HOST} \
        -r /workspace/requirements-app.txt

# ---------------------------------------------------------------------------
FROM base AS builder

RUN yum install -y --setopt=tsflags=nodocs gcc gcc-c++ make \
    && yum clean all && \
    rm -rf /var/cache/yum
RUN yum install -y --setopt=tsflags=nodocs ccache || true

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ENV NUITKA_CACHE_DIR=/var/cache/nuitka \
    CCACHE_DIR=/root/.ccache \
    NUITKA_FILE_TIMEOUT=1800 \
    COMPILE_MIN_BYTES=2048

RUN /opt/conda/bin/pip install --no-cache-dir --timeout=300 --retries=15 \
        ${PIP_INDEX_URL:+-i} ${PIP_INDEX_URL:+$PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host} ${PIP_TRUSTED_HOST:+$PIP_TRUSTED_HOST} \
        nuitka ordered-set zstandard

COPY app /workspace/app
COPY app/app-deploy/compiled-common /tmp/compiled-common
# Windows 检出的 .sh 常带 CRLF，内核会去找 /bin/sh\r → cannot execute: required file not found。
# 直接用 conda Python 跑 compile_app.py，不依赖 shebang。
# 分档并行编译；Nuitka/ccache 走 BuildKit cache mount，重复 --build 可复用。
RUN --mount=type=cache,target=/var/cache/nuitka \
    --mount=type=cache,target=/root/.ccache \
    set -e; \
    if command -v ccache >/dev/null 2>&1; then \
      for _ccache_lib in /usr/lib64/ccache /usr/lib/ccache; do \
        if [ -x "${_ccache_lib}/gcc" ]; then export PATH="${_ccache_lib}:${PATH}"; break; fi; \
      done; \
    fi; \
    find /tmp/compiled-common -type f \( -name '*.sh' -o -name '*.py' \) -exec sed -i 's/\r$//' {} +; \
    PYTHON=/opt/conda/bin/python /opt/conda/bin/python /tmp/compiled-common/compile_app.py /workspace \
      --app-dir /workspace/app --keep-file /tmp/compiled-common/compile_keep_py.txt; \
    rm -rf /tmp/compiled-common; \
    find /workspace/app -type d \( -name '__pycache__' -o -name '*.build' -o -name '*.dist' \) -prune -exec rm -rf {} + || true; \
    test -f /workspace/app/.compile_report.txt

# ---------------------------------------------------------------------------
FROM base AS runtime

COPY --from=builder /workspace/app /workspace/app
RUN mkdir -p /workspace/configs /workspace/logs

EXPOSE 8083

CMD ["/opt/conda/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
