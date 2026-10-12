#!/bin/sh
# 交付前：只编 app/services。不打运行镜像（现场 compose up --build 再打）。
# Usage:
#   ./compile.sh mx
#   ./compile.sh nvidia
#
# Do not use the host Python. 沐曦 = 本目录 compile 镜像内 conda 3.12；英伟达 = CPython 3.11.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
TARGET="${1:-}"
case "$TARGET" in
  mx|nvidia) ;;
  *)
    echo "usage: $0 mx|nvidia" >&2
    exit 2
    ;;
esac

COMPOSE_DIR="${SCRIPT_DIR}/docker-${TARGET}"
COMPILE_COMPOSE="docker-compose.compile.yml"

cd "${COMPOSE_DIR}"
if command -v sed >/dev/null 2>&1; then
  find "${SCRIPT_DIR}" -maxdepth 1 -type f \( -name '*.sh' -o -name '*.py' \) -exec sed -i 's/\r$//' {} + 2>/dev/null || true
fi

ENV_ARGS=""
if [ -f .env.nvidia_bak ]; then
  ENV_ARGS="--env-file .env"
elif [ -f "${SCRIPT_DIR}/../.env" ]; then
  ENV_ARGS="--env-file ${SCRIPT_DIR}/../.env"
fi

echo "[compile] build compile-only image (same Python as runtime base), then Nuitka app/services"
# shellcheck disable=SC2086
docker compose ${ENV_ARGS} -f "${COMPILE_COMPOSE}" build
echo "[compile] run Nuitka on app/services (writes .so, deletes compiled .py)"
# shellcheck disable=SC2086
docker compose ${ENV_ARGS} -f "${COMPILE_COMPOSE}" run --rm compile-services
echo "[compile] done. Runtime image is NOT built here; deploy with docker-compose-${TARGET}.yml --build"
echo "[compile] Check app/services/.compile_report.txt"
