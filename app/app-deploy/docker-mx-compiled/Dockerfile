# =============================================================================
# 吉泰沐曦：业务 Python Cython 编译进镜像（多阶段，最终层无源码 COPY）
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
COPY requirements-人脸识别-CPU.txt /workspace/requirements-face-cpu.txt
RUN /opt/conda/bin/pip install --upgrade pip setuptools wheel \
    && /opt/conda/bin/pip install --no-cache-dir --timeout=300 --retries=15 \
        ${PIP_INDEX_URL:+-i} ${PIP_INDEX_URL:+$PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host} ${PIP_TRUSTED_HOST:+$PIP_TRUSTED_HOST} \
        -r /workspace/requirements-app.txt -r /workspace/requirements-face-cpu.txt

# ---------------------------------------------------------------------------
FROM base AS builder

RUN yum install -y --setopt=tsflags=nodocs gcc gcc-c++ make \
    && yum clean all && \
    rm -rf /var/cache/yum

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
RUN /opt/conda/bin/pip install --no-cache-dir --timeout=300 --retries=15 \
        ${PIP_INDEX_URL:+-i} ${PIP_INDEX_URL:+$PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host} ${PIP_TRUSTED_HOST:+$PIP_TRUSTED_HOST} \
        cython

COPY app /workspace/app
COPY app/app-deploy/compiled-common /tmp/compiled-common
RUN set -e; \
    chmod +x /tmp/compiled-common/compile_app.sh; \
    PYTHON=/opt/conda/bin/python /tmp/compiled-common/compile_app.sh /workspace /workspace/app; \
    rm -rf /tmp/compiled-common /workspace/.cython_build; \
    find /workspace -maxdepth 1 -name '.cython_setup_*.py' -delete || true; \
    find /workspace/app -type d -name '__pycache__' -prune -exec rm -rf {} + || true; \
    test -f /workspace/app/.compile_report.txt

# ---------------------------------------------------------------------------
FROM base AS runtime

COPY --from=builder /workspace/app /workspace/app
RUN mkdir -p /workspace/configs /workspace/logs

EXPOSE 8083

CMD ["/opt/conda/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
