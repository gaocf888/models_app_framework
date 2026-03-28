FROM nvidia/cuda:13.2.0-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV WORKSPACE=/workspace

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR ${WORKSPACE}

# 创建目录
RUN mkdir -p ${WORKSPACE}/{config,scripts,logs,models}

# 复制项目文件
COPY config/ ${WORKSPACE}/config/
COPY scripts/ ${WORKSPACE}/scripts/
COPY requirements.txt ${WORKSPACE}/

# 安装依赖
RUN pip3 install --no-cache-dir -r requirements.txt

# 设置脚本权限
RUN chmod +x ${WORKSPACE}/scripts/*.py

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python3 ${WORKSPACE}/scripts/health.py || exit 1

EXPOSE 8000

CMD ["python3", "/workspace/scripts/start.py", "start"]