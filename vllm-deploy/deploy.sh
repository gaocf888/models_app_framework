#!/bin/bash
# 一键构建并启动 vLLM 服务（Docker Compose，与 README 唯一推荐部署方式一致）

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/docker"

if [ ! -f "$SCRIPT_DIR/.env" ] && [ -f "$SCRIPT_DIR/.env.example" ]; then
  echo "提示: 尚未找到 .env，可从 .env.example 复制: cp \"$SCRIPT_DIR/.env.example\" \"$SCRIPT_DIR/.env\""
fi

ENV_FILE_ARGS=()
if [ -f "$SCRIPT_DIR/.env" ]; then
  ENV_FILE_ARGS=(--env-file "$SCRIPT_DIR/.env")
fi

if docker compose version &>/dev/null; then
  docker compose "${ENV_FILE_ARGS[@]}" up -d --build
elif command -v docker-compose &>/dev/null; then
  docker-compose "${ENV_FILE_ARGS[@]}" up -d --build
else
  echo "错误: 需要 Docker Compose（docker compose 或 docker-compose）"
  exit 1
fi

echo "已启动。查看日志: cd \"$SCRIPT_DIR/docker\" && docker compose logs -f"
