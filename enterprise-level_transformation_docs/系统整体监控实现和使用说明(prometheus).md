# 系统整体监控实现和使用说明（Prometheus）

> **文档性质**：面向运维、交付与研发的**整体监控实现说明与日常使用手册**。  
> **覆盖范围**：应用侧 Prometheus 指标埋点、独立监控栈 `monitoring-deploy/`（Prometheus / Grafana / Alertmanager / Blackbox）、看板解读、告警与排障。  
> **方案原文**：[`docs/系统Prometheus资源监控实现方案.md`](../docs/系统Prometheus资源监控实现方案.md)  
> **运维启停细则**：[`monitoring-deploy/README.md`](../monitoring-deploy/README.md)  
> **部署顺序对照**：[`项目整体部署运维手册.md`](./项目整体部署运维手册.md)

**版本**：与仓库 `monitoring-deploy/` 交付对齐（2026-07；Phase 0～3 已落地）

---

## 1. 结论先看

| 问题 | 结论 |
|------|------|
| 监控用来做什么？ | 观测 **QPS / 延迟 / 错误率 / 业务量 / 服务存活**，支撑容量与排障 |
| 应用侧做了什么？ | `prometheus_client` 打点 + `GET /metrics` 暴露（**默认随 models-app 可用**） |
| 如何「真正用起来」？ | 部署 **`monitoring-deploy/`**：Prometheus 采集 → Grafana 展示 → Alertmanager 通知 |
| 是否影响业务？ | 监控栈挂掉 **不影响** 推理 / RAG / NL2SQL；业务未起则 Targets 为 DOWN |
| 旧方案？ | `vllm-deploy --profile monitoring` **已废弃**，勿在新环境使用 |

---

## 2. 架构与职责分层

```text
业务代码 .inc() / .observe()
        ↓
进程内 Registry（app/core/metrics.py）
        ↓
GET /metrics（models-app:8083、vllm-service:8000）
        ↓
Prometheus（scrape + 规则评估）  ← monitoring-deploy
        ├─→ Grafana（看板）
        └─→ Alertmanager（Webhook 通知）
Blackbox → 对 /health 做存活探测
```

| 层级 | 目录 / 组件 | 职责 |
|------|-------------|------|
| 埋点与暴露 | `app/core/metrics.py`、`app/main.py` | 定义指标；HTTP 中间件与业务路径打点；`/metrics` |
| path 高基数治理 | `app/core/metrics_path.py` | 优先路由模板，否则折叠 UUID/数字段 |
| 采集与存储 | `monitoring-prometheus` | 拉取、TSDB、告警规则 |
| 展示 | `monitoring-grafana` | 预置 4 套看板 |
| 通知 | `monitoring-alertmanager` | 路由告警到 Webhook（企微/钉钉等需现场配置） |
| 黑盒 | `monitoring-blackbox` | HTTP 探测 app / vLLM / MinerU 健康检查 |
| 节点指标 | `monitoring-node-exporter`（默认启动） | 宿主机 CPU/内存/磁盘等 |

**推荐启动顺序**（监控放最后）：

```text
EasySearch → vLLM →（可选 MinerU）→ 应用栈 → monitoring-deploy
```

---

## 3. 应用侧实现说明（研发 / 联调）

### 3.1 统一打点模式

1. 在 `app/core/metrics.py` 声明 `Counter` / `Histogram`（含 label）。
2. 在业务成功 / 失败路径调用 `.inc()` 或 `.observe(seconds)`。
3. `app/main.py` 提供：
   - `GET /metrics` → `generate_latest()`（Prometheus 文本）
   - HTTP 中间件自动累加 `http_requests_total` / `http_request_latency_seconds`（`path` 经 `metrics_path_label` 归一化）

### 3.2 主要指标一览

| 域 | 指标名（节选） | 含义 |
|----|----------------|------|
| HTTP | `http_requests_total`、`http_request_latency_seconds` | 全站请求量与延迟 |
| LLM | `llm_requests_total`、`llm_request_latency_seconds` | 按 `model` 的调用次数与耗时 |
| RAG | `rag_queries_total`、`rag_semantic_recall_total`、`rag_rerank_total` 等 | 检索 / 召回 / 重排 |
| NL2SQL | `nl2sql_queries_total`、`nl2sql_query_errors_total` | 问数次数与错误 |
| Analysis | `analysis_requests_total`、`analysis_node_latency_seconds`、`analysis_degrade_total` | 综合分析请求、节点耗时、降级 |
| Trace 运维 | `analysis_trace_queries_total` 等 | Trace 查询与缓存（配套 recording/告警规则） |
| 小模型 | `small_model_frames_processed_total` | 按 `model_name` 的帧处理量 |

完整定义以 `app/core/metrics.py` 为准。

### 3.3 打点示例

**HTTP（中间件，几乎所有请求）**：请求结束后按 method / 归一化 path / status 计数，并观察延迟。

**LLM（`app/llm/client.py`）**：每次 vLLM HTTP 调用后：

- `LLM_REQUEST_COUNT.labels(model=...).inc()`
- `LLM_REQUEST_LATENCY.labels(model=...).observe(duration)`

**NL2SQL（`app/services/nl2sql_service.py`）**：查询入口 `NL2SQL_QUERY_COUNT.inc()`；错误路径累加 `NL2SQL_QUERY_ERROR_COUNT`。

### 3.4 不启监控栈时如何自检

```bash
curl -s "http://127.0.0.1:${APP_PORT:-8083}/metrics" | head
curl -s "http://127.0.0.1:8000/metrics" | head
```

有数据即表示埋点与暴露正常；**长期趋势与告警仍依赖 `monitoring-deploy`**。

> 说明：多 `UVICORN_WORKERS` 时各 worker 进程指标相互独立，`/metrics` 默认只反映处理该连接的 worker；生产若强依赖精确全局计数，需另行评估 multiprocess 模式（当前默认单 worker 或接受此限制）。

---

## 4. 监控栈部署与使用（运维）

### 4.1 交付目录

```text
monitoring-deploy/
  docker-compose.yml
  .env.example
  prometheus/prometheus.yml
  prometheus/rules/*.yml
  grafana/dashboards/*.json
  grafana/provisioning/...
  alertmanager/alertmanager.yml
  blackbox/blackbox.yml
  scripts/check-baseline.sh | check-baseline.ps1
  README.md
```

### 4.2 前置条件（Phase 0）

业务栈已起，且 Docker 外部网络与各栈 `.env` 一致（常见：`docker_vllm-network`、`ai-stack`、`mineru-stack`）。

```bash
# Linux / Git Bash
bash monitoring-deploy/scripts/check-baseline.sh

# Windows PowerShell
powershell -File monitoring-deploy/scripts/check-baseline.ps1
```

期望：`models-app`、`vllm-service` 的 `/health` 与 `/metrics` 可达。

### 4.3 启动

```bash
cd monitoring-deploy
cp .env.example .env
# 必改：GF_SECURITY_ADMIN_PASSWORD
# 建议：PROMETHEUS_DATA_HOST_PATH / GRAFANA_DATA_HOST_PATH / ALERTMANAGER_DATA_HOST_PATH
# 核对：VLLM_DOCKER_NETWORK 等与 app、vllm .env 一致

docker network create docker_vllm-network 2>/dev/null || true
docker network create ai-stack 2>/dev/null || true
docker network create mineru-stack 2>/dev/null || true

mkdir -p /aidata/data/prometheus /aidata/data/grafana /aidata/data/alertmanager   # 按 .env 路径
bash scripts/prepare-data-dirs.sh   # 必须：否则 Prometheus permission denied
docker compose --env-file .env up -d
# node-exporter 已随默认栈启动；Prometheus 含 job_name: node
```

昇腾 / 专网步骤亦可对照 `README-DEPLOY-ASCEND.md` §6.5。

### 4.4 默认端口与入口

| 服务 | 端口 | 入口 |
|------|------|------|
| Prometheus | 9091 | `http://<host>:9091` → **Status → Targets** |
| Grafana | 3000 | `http://<host>:3000`（用户/密码见 `.env`） |
| Alertmanager | 9093 | `http://<host>:9093` |
| Blackbox | 9115 | 一般无需直接访问 |
| Node Exporter | 9100 | 宿主机节点指标（默认启动） |

**安全**：上述端口仅建议管理网 / 内网开放；`/metrics` 通常无业务鉴权，勿暴露公网。

### 4.5 停止

```bash
cd monitoring-deploy
docker compose --env-file .env down
# 数据在 bind 目录或 named volume 中，down 默认不删业务指标历史
```

---

## 5. Grafana 看板使用说明

登录 Grafana 后，文件夹 **Models App** 下预置：

| 看板 | 用途 | 关注什么 |
|------|------|----------|
| **01 App Overview** | 系统总览 | HTTP QPS、5xx 比例、延迟 P95、scrape `up`、Blackbox 探测 |
| **02 LLM** | 大模型 | `llm_requests_total` QPS、延迟 P95（按 model）、vLLM `up` |
| **03 RAG and NL2SQL** | 检索与问数 | RAG QPS / 召回 / 重排；NL2SQL QPS 与错误率 |
| **04 Analysis and Trace** | 综合分析 | 分析请求与降级、节点延迟、Trace 成功率 recording、小模型帧率 |

**日常用法建议**：

1. 先看 **01**：Targets / probe 是否绿、5xx 是否抬升。
2. 客服或推理慢：看 **02** 的 LLM P95 与 vLLM 存活。
3. 知识问答异常：看 **03** RAG；报表问数异常：看 NL2SQL 错误率。
4. 综合分析 / 降级：看 **04**。

数据源已通过 provisioning 指向 `http://prometheus:9091`，一般无需手工添加。

---

## 6. 告警与通知

### 6.1 规则文件（Prometheus 加载）

| 文件 | 内容 |
|------|------|
| `monitoring-deploy/prometheus/rules/app-http-alerts.yml` | `ModelsAppDown` / `VllmDown`、全局与 chatbot 5xx |
| `monitoring-deploy/prometheus/rules/app-business-alerts.yml` | LLM P95、NL2SQL 错误率、Analysis 失败率、小模型空闲、Blackbox 失败 |
| `monitoring-deploy/prometheus/rules/analysis-trace-alert-rules.yml` | Trace 成功率 / P95 / 缓存命中等（与 `configs/monitoring/` 同步） |

修改 `configs/monitoring/analysis-trace-alert-rules.yml` 后，须同步到 `monitoring-deploy/prometheus/rules/`，并：

```bash
curl -X POST http://127.0.0.1:9091/-/reload
```

### 6.2 Alertmanager

配置文件：`monitoring-deploy/alertmanager/alertmanager.yml`。

默认 `webhook_configs.url` 为**占位地址**，不会真正发到业务群。现场请改为企业微信 / 钉钉 / 自建网关 Webhook，然后：

```bash
docker compose --env-file .env up -d alertmanager
# 或重启 alertmanager 容器使配置生效
```

### 6.3 人为验证告警

```bash
docker stop models-app
# 等待 ≥2 分钟，Prometheus Alerts / Alertmanager 应出现 ModelsAppDown
docker start models-app
```

---

## 7. scrape 与网络要点（易错）

1. **容器内端口**：`models-app` 监听 **8083**（宿主机映射默认也是 8083）；Prometheus 用 `models-app:8083`，不要写成 8080。
2. **网络名**：Prometheus 须加入 `VLLM_DOCKER_NETWORK`（默认 `docker_vllm-network`），才能 DNS 解析 `models-app` 与 `vllm-service`。以 `docker network ls` 与 app `.env` 为准。
3. **未部署 MinerU**：注释 `prometheus.yml` 中 blackbox 的 `mineru-api` 目标，避免 `BlackboxProbeFailed` 噪声。
4. **models-app-gpu**：默认未 scrape；启用时按 `prometheus.yml` 内注释打开对应 job。
5. **EasySearch**：HTTPS + 账号密码，见 `prometheus/optional-easysearch.fragment.yml`（勿把密码提交公开仓库）。
6. **NPU 指标**：未打入默认 compose（与驱动/厂商包强绑定）；**node-exporter 已默认启动**采集宿主机指标，NPU exporter 按华为文档另接。

---

## 8. 验收清单

| # | 检查项 | 期望 |
|---|--------|------|
| 1 | Phase 0 基线脚本 | app + vLLM `/metrics` OK |
| 2 | `curl http://127.0.0.1:9091/-/ready` | Ready |
| 3 | Prometheus Targets | `models-app`、`vllm` 为 **UP** |
| 4 | Grafana 登录 | 可见 4 个 Models App 看板且有近 15 分钟曲线（有流量时） |
| 5 | Alertmanager UI | 可打开；配置真实 Webhook 后可收到测试告警 |
| 6 | 停 `models-app` ≥2m | `ModelsAppDown` 触发 |
| 7 | 业务功能 | 监控 down 时对话 / RAG 仍可用 |

---

## 9. 常见问题

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| Target DOWN | 网络名不一致 / 业务未起 / 端口错误 | 核对 `.env` 网络；`docker exec` 内 `curl models-app:8083/metrics` |
| Prometheus crash：`queries.active: permission denied` | bind 目录属主为 root，容器内 nobody 无法写 | `bash scripts/prepare-data-dirs.sh` 后重启 |
| Grafana 无数据 | Prometheus 无序列 / 时间范围 / 数据源 | Targets 先 UP；看 Query Inspector；确认 datasource uid `prometheus` |
| 告警不通知 | Webhook 仍为占位 | 改 `alertmanager.yml` |
| `/metrics` 有数但曲线空 | 无近期流量或 job 标签过滤过严 | 调大时间窗；对看板 PromQL 做 `curl` 验证 |
| path 序列暴涨 | 旧版本用原始 URL path | 升级含 `metrics_path.py` 的应用镜像并重启 |
| 与旧 vLLM Prometheus 冲突 | 旧 profile 占 9090、本栈默认 9091；若仍冲突检查端口占用 | 停用 `vllm-deploy` 的 monitoring profile |

---

## 10. 专网 / 离线镜像

有网机拉取并打包（版本以 `docker-compose.yml` 为准）：

```bash
docker pull prom/prometheus:v2.54.1
docker pull grafana/grafana:11.2.0
docker pull prom/alertmanager:v0.27.0
docker pull prom/blackbox-exporter:v0.25.0

docker save -o monitoring-images.tar \
  prom/prometheus:v2.54.1 grafana/grafana:11.2.0 \
  prom/alertmanager:v0.27.0 prom/blackbox-exporter:v0.25.0
```

专网：`docker load -i monitoring-images.tar` 后按 §4.3 启动。

---

## 11. 相关文档索引

| 文档 | 说明 |
|------|------|
| [`docs/系统Prometheus资源监控实现方案.md`](../docs/系统Prometheus资源监控实现方案.md) | 方案、分期、验收定义 |
| [`monitoring-deploy/README.md`](../monitoring-deploy/README.md) | 启停、配置、离线 |
| [`项目整体部署运维手册.md`](./项目整体部署运维手册.md) | 全栈部署顺序与运维 |
| [`README-DEPLOY-ASCEND.md`](../README-DEPLOY-ASCEND.md) §6.5 | 昇腾现场监控步骤 |
| `app/core/metrics.py` | 指标权威定义 |
| `configs/monitoring/README.md` | Trace 规则同步说明 |
| `docs/大小模型应用技术架构与实现方案.md` §5 | PromQL / 告警样例补充 |

---

## 12. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07 | 首版：对齐 `monitoring-deploy/` Phase 0～3 落地；明确废弃 vLLM 内嵌 monitoring profile |
