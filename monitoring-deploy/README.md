# monitoring-deploy — 系统 Prometheus / Grafana / Alertmanager 监控栈

权威方案：[`docs/系统Prometheus资源监控实现方案.md`](../docs/系统Prometheus资源监控实现方案.md)

本目录实现方案 **Phase 0～3** 的运维交付物：独立于业务 compose，采集 `models-app` / `vllm-service` 的 `/metrics`，黑盒探测健康检查，Grafana 看板与 Alertmanager 告警路由。

> **废弃说明**：`vllm-deploy` 内嵌 `profiles: monitoring` 的 Prometheus **已废弃**，请统一使用本目录。

---

## 1. 组件

| 服务 | 容器名 | 默认端口 | 说明 |
|------|--------|----------|------|
| Prometheus | `monitoring-prometheus` | 9090 | 采集 + 规则 |
| Grafana | `monitoring-grafana` | 3000 | 看板 |
| Alertmanager | `monitoring-alertmanager` | 9093 | 告警通知 |
| Blackbox | `monitoring-blackbox` | 9115 | HTTP 健康探针 |
| Node Exporter（可选） | `monitoring-node-exporter` | 9100 | `--profile infra` |

---

## 2. Phase 0：基线检查

业务栈先启动后，在仓库根或本目录执行：

```bash
# Git Bash / Linux
bash monitoring-deploy/scripts/check-baseline.sh

# 或手工
curl -s "http://127.0.0.1:${APP_PORT:-8083}/metrics" | head
curl -s "http://127.0.0.1:8000/metrics" | head
docker network ls | grep -E 'vllm|ai-stack|mineru'
```

期望：`models-app`、`vllm-service` 的 `/metrics` 可访问；外部网络名与 `.env` 一致（默认 `docker_vllm-network`、`ai-stack`、`mineru-stack`）。

---

## 3. 启动（Phase 1+）

```bash
cd monitoring-deploy
cp .env.example .env
# 修改 GF_SECURITY_ADMIN_PASSWORD、数据目录、网络名

# 外部网络不存在时先创建（业务栈通常已创建）
docker network create docker_vllm-network 2>/dev/null || true
docker network create ai-stack 2>/dev/null || true
docker network create mineru-stack 2>/dev/null || true

mkdir -p /aidata/data/prometheus /aidata/data/grafana /aidata/data/alertmanager

docker compose --env-file .env up -d
```

可选节点指标：

```bash
docker compose --env-file .env --profile infra up -d
# 并在 prometheus/prometheus.yml 取消 node job 注释后 reload：
# curl -X POST http://127.0.0.1:9090/-/reload
```

---

## 4. 验收

| 检查 | 命令 / 地址 | 期望 |
|------|-------------|------|
| Prometheus ready | `curl -s http://127.0.0.1:9090/-/ready` | Prometheus Server is Ready |
| Targets | 浏览器 `http://127.0.0.1:9090/targets` | `models-app`、`vllm` **UP** |
| Grafana | `http://127.0.0.1:3000` | 登录后见文件夹 Models App 下 4 个看板 |
| Alertmanager | `http://127.0.0.1:9093` | UI 可打开 |
| 人为宕机告警 | `docker stop models-app` 等待 ≥2m | `ModelsAppDown` 触发 |

看板清单：

1. **01 App Overview** — HTTP QPS / 5xx / P95 / scrape / blackbox  
2. **02 LLM** — LLM QPS 与延迟、vLLM up  
3. **03 RAG and NL2SQL** — RAG / NL2SQL  
4. **04 Analysis and Trace** — Analysis、Trace recording、小模型帧  

---

## 5. 配置要点

### 5.1 scrape

见 `prometheus/prometheus.yml`：

- `models-app:8083/metrics`（容器内端口 **8083**，非宿主机映射误解）
- `vllm-service:8000/metrics`
- Blackbox：`/health`（app / vllm / mineru）

未部署 MinerU 时，请注释 blackbox 中 `mineru-api` 行，避免 `BlackboxProbeFailed` 噪声。  
启用 `models-app-gpu` 时，按文件内注释取消对应 job。

### 5.2 告警规则

| 文件 | 内容 |
|------|------|
| `prometheus/rules/app-http-alerts.yml` | up / HTTP 5xx |
| `prometheus/rules/app-business-alerts.yml` | LLM / NL2SQL / Analysis / 小模型 / blackbox |
| `prometheus/rules/analysis-trace-alert-rules.yml` | 与 `configs/monitoring/` 同步的 Trace 规则 |

修改 `configs/monitoring/analysis-trace-alert-rules.yml` 后，请同步复制到本目录 `prometheus/rules/`。

### 5.3 Alertmanager 通知

编辑 `alertmanager/alertmanager.yml` 中 `webhook_configs.url`（企业微信 / 钉钉 / 自建网关）。  
默认指向占位地址，**不会真正发通知**。

### 5.4 网络

Prometheus / Blackbox 加入：

- `VLLM_DOCKER_NETWORK`（默认 `docker_vllm-network`）— 解析 `models-app`、`vllm-service`
- `RAG_DOCKER_NETWORK` / `MINERU_DOCKER_NETWORK` — 黑盒与扩展用

若现场 vLLM 网络名不是 `docker_vllm-network`，以 `docker network ls` 与 app `.env` 中 `VLLM_DOCKER_NETWORK` 为准。

---

## 6. 停止

```bash
docker compose --env-file .env down
# 保留数据卷/bind 目录；彻底清理需手动删 /aidata/data/prometheus 等
```

---

## 7. 专网 / 昇腾离线

有网机器：

```bash
docker pull prom/prometheus:v2.54.1
docker pull grafana/grafana:11.2.0
docker pull prom/alertmanager:v0.27.0
docker pull prom/blackbox-exporter:v0.25.0
# 可选
docker pull prom/node-exporter:v1.8.2

docker save -o monitoring-images.tar \
  prom/prometheus:v2.54.1 grafana/grafana:11.2.0 \
  prom/alertmanager:v0.27.0 prom/blackbox-exporter:v0.25.0
```

专网：`docker load -i monitoring-images.tar` 后按 §3 启动。

NPU 专用 exporter（昇腾）因厂商包与现场驱动强绑定，**未打入默认 compose**；节点级资源请先用 `--profile infra` 的 node-exporter，NPU 指标按华为文档另行接入并增加 scrape job。

---

## 8. 与应用打点的关系

- 应用继续通过 `app/core/metrics.py` + `GET /metrics` 暴露指标。
- HTTP `path` 标签已做路由模板 / 动态段折叠（`app/core/metrics_path.py`），降低高基数风险。
- 监控栈挂掉 **不影响** 业务推理；业务未起则 Targets 为 DOWN。
