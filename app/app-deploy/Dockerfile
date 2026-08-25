# =============================================================================
# 默认在线应用镜像：大模型应用依赖（无 CUDA PyTorch / 无 Ultralytics）
# =============================================================================
# 构建上下文（context）：必须是仓库根目录 models_app_framework
#   docker compose 中已写 context: ../..  relative to app/app-deploy
#
# 包含：
#   - requirements-大模型应用.txt（FastAPI、RAG、ES 客户端、sentence-transformers 等）
#     ※ pip 安装须在「有网」构建机完成；离线服务器不执行 pip
#   - app/ 源码、configs/ YAML（COPY 为 fallback；运行时 bind mount 覆盖）
#     ※ 运行时默认 bind mount 宿主机 /opt/deploy/models_app_framework/{app,configs}（见 compose）
#
# 不包含：小模型 GPU 推理依赖（YOLO/Ultralytics）→ profile small-model-gpu
# 包含：CPU 人脸识别（/face/*）→ requirements-人脸识别-CPU.txt
# =============================================================================

FROM python:3.11-slim-bookworm
# 针对沐曦卡服务器，因app-deploy应用中由reranker重排序模型，为了推理速度，基础镜像使用沐曦的AI框架镜像(适配版torch)
# FROM cr.metax-tech.com/public-ai-release/maca/vllm-metax:0.17.0-maca.ai3.5.3.307-torch2.8-py312-kylinv11-amd64

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/workspace \
    PIP_DEFAULT_TIMEOUT=300

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libglib2.0-0 libsm6 libxext6 libgl1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 构建阶段若出现 json.decoder.JSONDecodeError（解析 PyPI 索引 JSON 失败），多为响应被截断/超时；
# 已加大 pip 超时与重试。仍不稳定时可指定镜像，例如：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#     --build-arg PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

COPY requirements-大模型应用.txt /workspace/requirements-app.txt
COPY requirements-人脸识别-CPU.txt /workspace/requirements-face-cpu.txt
RUN pip install --upgrade pip setuptools wheel \
    && pip install --no-cache-dir --timeout=300 --retries=15 \
        ${PIP_INDEX_URL:+-i} ${PIP_INDEX_URL:+$PIP_INDEX_URL} \
        ${PIP_TRUSTED_HOST:+--trusted-host} ${PIP_TRUSTED_HOST:+$PIP_TRUSTED_HOST} \
        -r /workspace/requirements-app.txt -r /workspace/requirements-face-cpu.txt

COPY app /workspace/app
COPY configs /workspace/configs
# 运行时 compose 将宿主机目录 mount 到 /workspace/app、/workspace/configs，覆盖上述 COPY。

EXPOSE 8083

# 生产 worker 数由 compose command 传入；此仅为镜像默认入口
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
