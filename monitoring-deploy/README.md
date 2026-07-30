# monitoring-deploy — 系统 Prometheus / Grafana / Alertmanager 监控栈

权威方案：[`docs/系统Prometheus资源监控实现方案.md`](../docs/系统Prometheus资源监控实现方案.md)

本目录实现方案 **Phase 0～3** 的运维交付物：独立于业务 compose，采集 `models-app` / `vllm-service` 的 `/metrics`，黑盒探测健康检查，Grafana 看板与 Alertmanager 告警路由。

> **废弃说明**：`vllm-deploy` 内嵌 `profiles: monitoring` 的 Prometheus **已废弃**，请统一使用本目录。

---

## 1. 组件

| 服务 | 容器名 | 默认端口 | 说明 |
|------|--------|----------|------|
| Prometheus | `monitoring-prometheus` | 9091 | 采集 + 规则 |
| Grafana | `monitoring-grafana` | 3000 | 看板 |
| Alertmanager | `monitoring-alertmanager` | 9093 | 告警通知 |
| Blackbox | `monitoring-blackbox` | 9115 | HTTP 健康探针 |
| Node Exporter | `monitoring-node-exporter` | 9100 | 宿主机 CPU/内存/磁盘等（默认启动） |
| DCGM Exporter（英伟达） | `monitoring-dcgm-exporter` | 9400 | **profile `gpu-nvidia`** |
| NPU Exporter（昇腾） | `monitoring-npu-exporter` | 8082 | **profile `gpu-ascend`** |

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

# 重要：bind 目录须给容器内 nobody(65534) / grafana(472) 写权限
# 否则 Prometheus 报错：open /prometheus/queries.active: permission denied
bash scripts/prepare-data-dirs.sh

docker compose --env-file .env up -d
```

可选：宿主机确认 node-exporter：

```bash
docker ps --filter name=monitoring-node-exporter
```

### 3.1 GPU / NPU 硬件监控（按卡型启用）

`node-exporter` **不含**显卡/加速卡。本仓库已接入：

| 卡型 | Compose profile | Exporter | Grafana 看板 |
|------|-----------------|----------|--------------|
| **英伟达** | `gpu-nvidia` | `nvidia/dcgm-exporter` | **06 NVIDIA GPU (DCGM)** |
| **昇腾** | `gpu-ascend` | `ascendai/npu-exporter` | **07 Ascend NPU** |
| **沐曦 / 寒武纪 / 其它** | — | **未内置** | 须单独增加 exporter + scrape job + 看板 |

**英伟达现场：**

```bash
# .env 中设置：COMPOSE_PROFILES=gpu-nvidia
# 或命令行：
docker compose --env-file .env --profile gpu-nvidia up -d
# 验收：Targets 中 job=dcgm UP；Grafana → 06 NVIDIA GPU (DCGM)
curl -s http://127.0.0.1:9400/metrics | head
```

前提：宿主机已装 NVIDIA 驱动 + nvidia-container-toolkit，`runtime: nvidia` 可用。

**昇腾现场：**

```bash
# .env 中设置：COMPOSE_PROFILES=gpu-ascend
# 并按驱动/MindCluster 版本调整 ASCEND_NPU_EXPORTER_IMAGE（见 .env.example）
docker compose --env-file .env --profile gpu-ascend up -d
# 验收：Targets 中 job=npu UP；Grafana → 07 Ascend NPU
curl -s http://127.0.0.1:8082/metrics | head
```

前提：宿主机可执行 `npu-smi info`；驱动/DCMI 路径与 `.env` 中 `ASCEND_*_HOST_PATH` 一致。  
**指标名随 npu-exporter 版本可能变化**；若看板无数据，在 Prometheus 用 `{job="npu"}` 查看实际序列名后微调 `07-npu-ascend.json`。

**其它加速卡：** 复制本仓库英伟达/昇腾模式，新增厂商 exporter 服务、在 `prometheus.yml` 增加 scrape、新增 Grafana JSON；勿假设 node-exporter 能覆盖。

未启用对应 profile 时，Targets 里 `dcgm` / `npu` 为 **DOWN 属预期**，可忽略。

---

## 4. 验收

| 检查 | 命令 / 地址 | 期望                                       |
|------|-------------|------------------------------------------|
| Prometheus ready | `curl -s http://127.0.0.1:9091/-/ready` | Prometheus Server is Ready               |
| Targets | 浏览器 `http://127.0.0.1:9091/targets` | `models-app`、`vllm`、`node` **UP**        |
| Grafana | `http://127.0.0.1:3000` | 登录后见文件夹 Models App 下看板（含主机资源）；账密见 `.env` |
| Alertmanager | `http://127.0.0.1:9093` | UI 可打开                                   |
| 人为宕机告警 | `docker stop models-app` 等待 ≥2m | `ModelsAppDown` 触发                       |

看板清单：

1. **01 App Overview** — HTTP QPS / 5xx / P95 / scrape / blackbox  
2. **02 LLM** — LLM QPS 与延迟、vLLM up  
3. **03 RAG and NL2SQL** — RAG / NL2SQL  
4. **04 Analysis and Trace** — Analysis、Trace recording、小模型帧  
5. **05 Host Resources** — CPU / 内存 / Load / 磁盘 / 网络（node-exporter）  
6. **06 NVIDIA GPU (DCGM)** — 英伟达利用率 / 显存 / 温度 / 功耗（需 `gpu-nvidia`）  
7. **07 Ascend NPU** — 昇腾利用率 / HBM / 温度 / 功耗（需 `gpu-ascend`）

---

## 5. 配置要点

### 5.1 scrape

见 `prometheus/prometheus.yml`：

- `models-app:8083/metrics`（容器内端口 **8083**，非宿主机映射误解）
- `vllm-service:8000/metrics`
- Blackbox：`/health`（app / vllm / mineru）
- `node-exporter:9100`（主机）
- `dcgm-exporter:9400` / `npu-exporter:8082`（启用对应 GPU profile 后才 UP）

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

### 5.5 排障：permission denied / queries.active

```text
open /prometheus/queries.active: permission denied
panic: Unable to create mmap-ed active query log
```

原因：宿主机数据目录由 root 创建，`prom/prometheus` 以 **nobody (65534)** 运行，无法写 `/prometheus`。

```bash
bash scripts/prepare-data-dirs.sh
# 或手工：
# chown -R 65534:65534 /aidata/data/prometheus /aidata/data/alertmanager
# chown -R 472:472 /aidata/data/grafana
docker compose --env-file .env up -d
```

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
docker pull prom/node-exporter:v1.8.2
# 按现场卡型二选一
docker pull nvidia/dcgm-exporter:4.8.3
docker pull ascendai/npu-exporter:v7.3.2

docker save -o monitoring-images.tar \
  prom/prometheus:v2.54.1 grafana/grafana:11.2.0 \
  prom/alertmanager:v0.27.0 prom/blackbox-exporter:v0.25.0
```

专网：`docker load -i monitoring-images.tar` 后按 §3 启动。

NPU 专用 exporter（昇腾）因厂商包与现场驱动强绑定，**未打入默认 compose**；宿主机节点级资源由默认启动的 **node-exporter** 提供，NPU 指标按华为文档另行接入并增加 scrape job。

---

## 8. 与应用打点的关系

- 应用继续通过 `app/core/metrics.py` + `GET /metrics` 暴露指标。
- HTTP `path` 标签已做路由模板 / 动态段折叠（`app/core/metrics_path.py`），降低高基数风险。
- 监控栈挂掉 **不影响** 业务推理；业务未起则 Targets 为 DOWN。
