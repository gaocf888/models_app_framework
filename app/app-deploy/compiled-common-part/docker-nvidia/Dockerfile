# =============================================================================
# 英伟达：仅依赖运行镜像（不 COPY app / configs，由 compose 挂载）
# =============================================================================
# 构建上下文必须是仓库根。与 docker-nvidia/Dockerfile-nvidia 同一基座；现网 Dockerfile-nvidia 与 compiled-common/docker-nvidia-compiled 不改。
# =============================================================================

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

FROM python:3.11-slim-bookworm

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

RUN mkdir -p /workspace/app /workspace/configs /workspace/logs

EXPOSE 8083

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
