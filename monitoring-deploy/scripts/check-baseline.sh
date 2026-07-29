#!/usr/bin/env bash
# Phase 0：监控基线检查（宿主机执行）
set -euo pipefail

APP_PORT="${APP_PORT:-8083}"
VLLM_PORT="${VLLM_PORT:-8000}"
MINERU_PORT="${MINERU_PORT:-8009}"

echo "== Phase 0 baseline =="
echo "APP_PORT=${APP_PORT} VLLM_PORT=${VLLM_PORT} MINERU_PORT=${MINERU_PORT}"
echo

check_http() {
  local name="$1"
  local url="$2"
  if curl -fsS --max-time 5 "$url" >/dev/null 2>&1; then
    echo "[OK] ${name}: ${url}"
  else
    echo "[FAIL] ${name}: ${url}"
    return 1
  fi
}

fail=0
check_http "models-app /health" "http://127.0.0.1:${APP_PORT}/health" || fail=1
check_http "models-app /metrics" "http://127.0.0.1:${APP_PORT}/metrics" || fail=1
check_http "vllm /health" "http://127.0.0.1:${VLLM_PORT}/health" || fail=1
check_http "vllm /metrics" "http://127.0.0.1:${VLLM_PORT}/metrics" || fail=1

# MinerU 可选
if curl -fsS --max-time 3 "http://127.0.0.1:${MINERU_PORT}/health" >/dev/null 2>&1; then
  echo "[OK] mineru /health: http://127.0.0.1:${MINERU_PORT}/health"
else
  echo "[SKIP] mineru /health (not reachable on ${MINERU_PORT})"
fi

echo
echo "Docker networks (expect docker_vllm-network / ai-stack / mineru-stack):"
docker network ls --format '{{.Name}}' | grep -E 'vllm|ai-stack|mineru' || true

echo
if [[ "$fail" -eq 0 ]]; then
  echo "Phase 0 baseline: PASS (app + vllm metrics reachable)"
  exit 0
fi
echo "Phase 0 baseline: FAIL (fix app/vllm first, then monitoring-deploy)"
exit 1
