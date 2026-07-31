#!/usr/bin/env bash
# 为 bind 挂载数据目录设置官方镜像运行用户可写权限。
# Prometheus / Alertmanager 镜像默认 nobody (65534)；Grafana 默认 grafana (472)；
# Tempo 官方镜像默认用户 tempo (10001)。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-${ROOT_DIR}/.env}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # 仅导出 KEY=VALUE 行，忽略注释
  # shellcheck disable=SC1091
  source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" || true)
  set +a
fi

PROM_DIR="${PROMETHEUS_DATA_HOST_PATH:-/aidata/data/prometheus}"
GRAF_DIR="${GRAFANA_DATA_HOST_PATH:-/aidata/data/grafana}"
AM_DIR="${ALERTMANAGER_DATA_HOST_PATH:-/aidata/data/alertmanager}"
TEMPO_DIR="${TEMPO_DATA_HOST_PATH:-}"

echo "Preparing data dirs:"
echo "  prometheus    -> ${PROM_DIR} (uid 65534)"
echo "  grafana       -> ${GRAF_DIR} (uid 472)"
echo "  alertmanager  -> ${AM_DIR} (uid 65534)"

mkdir -p "$PROM_DIR" "$GRAF_DIR" "$AM_DIR"
chown -R 65534:65534 "$PROM_DIR"
chown -R 472:472 "$GRAF_DIR"
chown -R 65534:65534 "$AM_DIR"
chmod -R u+rwX "$PROM_DIR" "$GRAF_DIR" "$AM_DIR"

# Tempo：仅当 .env 配置了宿主机 bind 路径时处理（默认 named volume 无需 chown）
if [[ -n "${TEMPO_DIR}" && "${TEMPO_DIR}" != "tempo-data" ]]; then
  if [[ "${TEMPO_DIR}" == /* || "${TEMPO_DIR}" =~ ^[A-Za-z]: || "${TEMPO_DIR}" == ./* || "${TEMPO_DIR}" == ../* ]]; then
    echo "  tempo         -> ${TEMPO_DIR} (uid 10001)"
    mkdir -p "$TEMPO_DIR"
    chown -R 10001:10001 "$TEMPO_DIR" 2>/dev/null || chmod -R a+rwX "$TEMPO_DIR"
    chmod -R u+rwX "$TEMPO_DIR" || true
  fi
fi

echo "Done. Restart stack if containers were already failing:"
echo "  docker compose --env-file .env up -d"
# tracing profile:
echo "  COMPOSE_PROFILES=tracing docker compose --env-file .env up -d tempo"
