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
| Tempo（Trace） | `monitoring-tempo` | 3200 / OTLP 4318 | **profile `tracing`**；Grafana Explore → Tempo，按 `request_id`/`job_id` 查链路 |

请求/任务级 Trace 方案见 [`docs/系统执行轨迹与LangSmith观测改造方案.md`](../docs/系统执行轨迹与LangSmith观测改造方案.md)。

### 1.1 推荐路径：Redis + Tempo

| 层 | 职责 | 配置要点 |
|----|------|----------|
| **Redis** | 结构化轨迹 JSON、列表/统计、`/ops/traces*` | `EXECUTION_TRACE_BACKEND=redis` + 已有 `REDIS_URL`；TTL 默认/示例 `2880` 分钟 |
| **Tempo** | OTLP 瀑布图 / Node Graph（Grafana Explore） | `COMPOSE_PROFILES=tracing` 启 `monitoring-tempo`；应用 `EXECUTION_TRACE_OTLP_ENABLED=true` |

二者**并行、互不替代**：Redis 给运维 API；Tempo 给链路可视化。不要用业务 ES 做 Trace 热库。

启动与 profile 用法见 **§3.2**（`COMPOSE_PROFILES=tracing` 或 `--profile tracing`）。

应用侧打开 OTLP 后：探活 `GET /ops/traces-status`；详情 `GET /ops/traces/{request_id}`（Bearer `SERVICE_API_KEY`）；`meta.tempo_trace_id` 可对齐 Grafana Explore → Tempo（按 `request_id` / `job_id`）。

**网络注意**：同网用 `http://monitoring-tempo:4318`；不通时用 `http://host.docker.internal:4318`（已映射 `TEMPO_OTLP_HTTP_PORT`）。详见 §1 组件表与 §3.2。

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

> 根据需要监控的资源类型，部署分为3.1 基线栈(基础的应用) 和 3.2 可选组件(包括显卡和应用执行追踪)
> 没有特殊原因，默认选择 按照 3.2部署即可(根据显卡类型 选择gpu-nvidia/gpu-ascend，应用执行追踪Tracing都选择即可)

### 3.1 基线栈

默认拉起：Prometheus / Grafana / Alertmanager / Blackbox / node-exporter（**不含** GPU/NPU exporter、**不含** Tempo）。

```bash
cd monitoring-deploy
cp .env.example .env
# 修改 GF_SECURITY_ADMIN_PASSWORD、数据目录、网络名

# 外部网络不存在时先创建（业务栈通常已创建）
docker network create docker_vllm-network 2>/dev/null || true
docker network create ai-stack 2>/dev/null || true
docker network create mineru-stack 2>/dev/null || true

# bind 目录权限：nobody(65534) / grafana(472)；若配置 TEMPO_DATA_HOST_PATH 则含 Tempo(10001)
# 否则 Prometheus 可能报：open /prometheus/queries.active: permission denied
bash scripts/prepare-data-dirs.sh

docker compose --env-file .env up -d
```

确认：`docker ps --filter name=monitoring-node-exporter`

### 3.2 可选组件：用 Compose profile 启用

GPU/NPU、Tempo 均挂在 **profile** 上，**默认不启动**。启用方式二选一（可组合，逗号分隔）：

| 方式 | 做法 |
|------|------|
| **`.env` 持久化** | 写入 `COMPOSE_PROFILES=...`，再执行 `docker compose --env-file .env up -d` |
| **命令行临时** | `docker compose --env-file .env --profile <名> up -d`（可多次 `--profile`） |

可用 profile：

| Profile | 作用 | 验收要点 | 现场前提 / 额外配置 |
|---------|------|----------|---------------------|
| `gpu-nvidia` | DCGM Exporter | Targets `job=dcgm` UP；Grafana **06** | 宿主机 NVIDIA 驱动 + nvidia-container-toolkit |
| `gpu-ascend` | NPU Exporter | Targets `job=npu` UP；Grafana **07** | `npu-smi info` 可用；按需改 `ASCEND_*` / 镜像版本（见 `.env.example`） |
| `tracing` | Tempo（OTLP） | `monitoring-tempo` Up；`http://127.0.0.1:3200/ready` | 应用侧打开 OTLP（见下）；推荐与 Redis Trace 并用（§1.1） |

示例：

```bash
# A. 写在 .env（推荐现场固化）
# COMPOSE_PROFILES=tracing   # 部署应用追踪
# COMPOSE_PROFILES=gpu-ascend  # 部署显卡监控
# COMPOSE_PROFILES=tracing,gpu-ascend  # 同时部署应用追踪和显卡监控

docker compose --env-file .env up -d

# B. 命令行（不改 .env）
docker compose --env-file .env --profile tracing up -d   # 部署应用追踪
docker compose --env-file .env --profile gpu-nvidia up -d   # 部署英伟达显卡监控
docker compose --env-file .env --profile tracing --profile gpu-ascend up -d  # 同时部署应用追踪和晟腾显卡监控
docker compose --env-file .env --profile tracing --profile gpu-nvidia up -d  # 同时部署应用追踪和英伟达显卡监控
```

未启用对应 profile 时：无 `monitoring-tempo`、Targets 里 `dcgm`/`npu` **DOWN 均属预期**。

**`tracing` 时应用侧还须打开导出**（否则 Grafana 无 Trace；本目录只起 Tempo）：

```bash
# app/app-deploy/.env
EXECUTION_TRACE_ENABLED=true
EXECUTION_TRACE_BACKEND=redis
EXECUTION_TRACE_OTLP_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://monitoring-tempo:4318
# 跨网不通：http://host.docker.internal:4318
```

探活：`GET /ops/traces-status`；Grafana Explore → Tempo，按 `request_id` / `job_id` 查。

**说明：**
- `node-exporter` 不含显卡/加速卡；沐曦/寒武纪等未内置，须自增 exporter + scrape + 看板。
- 昇腾指标名随 npu-exporter 版本可能变化；看板无数据时在 Prometheus 用 `{job="npu"}` 核对后调 `07-npu-ascend.json`。

---

## 4. 验收

| 检查 | 命令 / 地址 | 期望                                       |
|------|-------------|------------------------------------------|
| Prometheus ready | `curl -s http://127.0.0.1:9091/-/ready` | Prometheus Server is Ready               |
| Targets | 浏览器 `http://127.0.0.1:9091/targets` | `models-app`、`vllm`、`node` **UP**        |
| Grafana | `http://127.0.0.1:3000` | 登录后见文件夹 Models App 下看板（含主机资源）；账密见 `.env` |
| Alertmanager | `http://127.0.0.1:9093` | UI 可打开                                   |
| Tempo（需 `tracing`） | `docker ps --filter name=monitoring-tempo`；Explore → Tempo | 容器 Up；按 `request_id` 可查到链路 |
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
