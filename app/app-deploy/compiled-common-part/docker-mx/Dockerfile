# =============================================================================
# 沐曦：仅依赖运行镜像（不 COPY app / configs，由 compose 挂载）
# =============================================================================
# 构建上下文必须是仓库根 models_app_framework。
# 与 docker-mx/Dockerfile-mx 同一基座；现网 Dockerfile-mx 与 compiled-common/docker-mx-compiled 不改。
# =============================================================================

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

FROM cr.metax-tech.com/public-ai-release/maca/vllm-metax:0.17.0-maca.ai3.5.3.307-torch2.8-py312-kylinv11-amd64

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

RUN mkdir -p /workspace/app /workspace/configs /workspace/logs

EXPOSE 8083

CMD ["/opt/conda/bin/python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
