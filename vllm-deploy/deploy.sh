#!/bin/bash
# 一键构建并启动 vLLM 服务（支持按平台选择 compose overlay）

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/docker"

PLATFORM="${VLLM_PLATFORM:-nvidia}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform)
      PLATFORM="$2"
      shift 2
      ;;
    *)
      echo "错误: 未知参数 $1"
      echo "用法: ./deploy.sh [--platform nvidia|cambricon|mthreads|ascend]"
      exit 1
      ;;
  esac
done

if [ ! -f "$SCRIPT_DIR/.env" ] && [ -f "$SCRIPT_DIR/.env.example" ]; then
  echo "提示: 尚未找到 .env，可从 .env.example 复制: cp \"$SCRIPT_DIR/.env.example\" \"$SCRIPT_DIR/.env\""
fi

ENV_FILE_ARGS=()
if [ -f "$SCRIPT_DIR/.env" ]; then
  ENV_FILE_ARGS=(--env-file "$SCRIPT_DIR/.env")
fi

COMPOSE_FILES=(-f docker-compose.yml)
case "$PLATFORM" in
  nvidia)
    COMPOSE_FILES+=(-f docker-compose.nvidia.yml)
    ;;
  cambricon)
    COMPOSE_FILES+=(-f docker-compose.cambricon.yml)
    ;;
  mthreads)
    COMPOSE_FILES+=(-f docker-compose.mthreads.yml)
    ;;
  ascend)
    COMPOSE_FILES+=(-f docker-compose.ascend.yml)
    ;;
  *)
    echo "错误: 不支持的平台 '$PLATFORM'"
    echo "当前支持: nvidia, cambricon, mthreads, ascend"
    exit 1
    ;;
esac

if docker compose version &>/dev/null; then
  docker compose "${ENV_FILE_ARGS[@]}" "${COMPOSE_FILES[@]}" up -d --build
elif command -v docker-compose &>/dev/null; then
  docker-compose "${ENV_FILE_ARGS[@]}" "${COMPOSE_FILES[@]}" up -d --build
else
  echo "错误: 需要 Docker Compose（docker compose 或 docker-compose）"
  exit 1
fi

echo "已启动（platform=$PLATFORM）。查看日志: cd \"$SCRIPT_DIR/docker\" && docker compose ${COMPOSE_FILES[*]} logs -f"
