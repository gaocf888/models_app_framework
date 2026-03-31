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
    PYTHONPATH=/workspace

WORKDIR /workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-大模型应用.txt /workspace/requirements-app.txt
RUN pip install --no-cache-dir -r /workspace/requirements-app.txt

COPY app /workspace/app
COPY configs /workspace/configs

EXPOSE 8083

# 生产 worker 数由 compose command 传入；此仅为镜像默认入口
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8083"]
