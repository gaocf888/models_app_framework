# 构建参数（与 docker-compose / docker build --build-arg 一致）
# BASE_IMAGE：英伟达环境默认 CUDA 运行时；国产算力请改为厂商提供的已适配镜像。
# VLLM_REQUIREMENTS_PROFILE：full=安装 requirements.txt（含 vLLM）；extras=仅 requirements-extras.txt。
ARG BASE_IMAGE=nvidia/cuda:12.3.0-runtime-ubuntu22.04
FROM ${BASE_IMAGE}

ARG VLLM_REQUIREMENTS_PROFILE=full

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV WORKSPACE=/workspace

# 系统依赖：python3 / pip / curl（部署文档要求基础镜像需具备可写层以完成安装）
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR ${WORKSPACE}

# 运行期目录；config/scripts 由 docker-compose 挂载，不在镜像内复制
RUN mkdir -p ${WORKSPACE}/{config,scripts,logs,models,media}

COPY requirements.txt requirements-extras.txt ${WORKSPACE}/

# 按构建参数选择依赖集合（默认 full = 英伟达式完整安装）
RUN set -eux; \
    if [ "${VLLM_REQUIREMENTS_PROFILE}" = "extras" ]; then \
      REQ="${WORKSPACE}/requirements-extras.txt"; \
    else \
      REQ="${WORKSPACE}/requirements.txt"; \
    fi; \
    pip3 install --no-cache-dir -r "${REQ}" \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn \
      --default-timeout=600 --retries=20

EXPOSE 8000

# 启动命令与健康检查在 docker-compose.yml 中配置；配置与脚本通过卷挂载提供
