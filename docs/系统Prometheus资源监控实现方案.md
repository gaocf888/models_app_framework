# 系统 Prometheus 资源监控实现方案

本文档面向本仓库 **多栈容器化部署**（`app/app-deploy`、`vllm-deploy`、可选 `mineru-deploy` 等），说明如何在现有 **Prometheus 格式指标埋点** 基础上，补齐 **采集、存储、看板与告警** 闭环，形成可运维的资源与业务监控能力。

**关联文档**

| 文档 / 代码 | 说明 |
|-------------|------|
| `app/core/metrics.py` | 应用侧指标定义（Counter / Histogram） |
| `app/main.py` | `GET /metrics` 暴露与 HTTP 中间件打点 |
| `vllm-deploy/docker/prometheus.yml` | 现有可选 Prometheus 配置（仅抓 vLLM） |
| `vllm-deploy/docker/docker-compose.yml` | `profiles: [monitoring]` 可选 Prometheus 服务 |
| `configs/monitoring/analysis-trace-alert-rules.yml` | Analysis Trace 告警 / recording 规则样例 |
| `docs/大小模型应用技术架构与实现方案.md` §5 | PromQL / Grafana 面板建议 |
| `README-DEPLOY-ASCEND.md` | 昇腾现场部署手册（落地后应增补监控章节） |

---

## 1. 背景与现状结论

### 1.1 目标

用标准指标衡量应用健康与业务量，支撑：

- **容量 / 性能**：QPS、延迟分布（P95）
- **可靠性**：错误率、降级次数、抓取目标存活（`up`）
- **业务量**：RAG / NL2SQL / Analysis / LLM 调用量等
- **告警**：基于规则的阈值与通知

### 1.2 现状分层

| 层级 | 做什么 | 现状 |
|------|--------|------|
| 应用进程 | `prometheus_client` 打点，暴露 `GET /metrics` | **已实现且默认可用** |
| Prometheus Server | 定时 scrape `/metrics`，存 TSDB、评估告警规则 | **仅 vLLM 栈可选**（`--profile monitoring`） |
| Grafana | 以 Prometheus 为数据源做面板 | **仓库内无部署 / 无仪表盘交付物** |
| Alertmanager | 告警路由与通知 | **未交付**（文档有规则样例） |

### 1.3 关键结论

1. **应用侧已完成 Prometheus 格式埋点与暴露**；业务代码在请求路径上 `.inc()` / `.observe()`，不启 Prometheus 时进程内指标也会增长，可用 `curl` 临时查看。
2. **缺少完整监控闭环**：默认无长期存储、趋势图、自动告警。
3. `vllm-deploy` 可选 Prometheus **默认只 scrape `vllm:8000/metrics`**，**未抓取 `models-app` 的 `/metrics`**。
4. 完整实现需要部署 **Prometheus（并配置 scrape 应用）** + **Grafana（展示）**；告警规则可挂 Prometheus，通知可用 Grafana 联系点或 Alertmanager。
5. **不建议**继续把全栈监控长期挂靠在 `vllm-deploy` 的 `monitoring` profile 上（应用跨多 Docker 网络，职责易耦合）。推荐新建独立 **`monitoring-deploy/`**。

### 1.4 边界（本方案约定）

- **不改**业务打点主路径（现有指标体系够用一期）；只补采集侧与展示侧。
- **一期不做**全量基础设施指标（node_exporter、NPU exporter 等放二期）。
- **Loki 日志栈**可并行规划，与本方案解耦，本文不展开。

---

## 2. 应用侧打点机制（已落地，完整实现时复用）

### 2.1 统一模式

```text
app/core/metrics.py 声明 Counter/Histogram
        ↓
业务代码关键路径 .inc() / .observe(seconds)
        ↓
app/main.py：HTTP 中间件打点 + GET /metrics → generate_latest()
```

### 2.2 示例

**HTTP 层（中间件，几乎所有请求）** — `app/main.py`：

- `http_requests_total{method,path,status}`
- `http_request_latency_seconds{method,path}`

**LLM 调用** — `app/llm/client.py`（`VLLMHttpClient`）：

- `llm_requests_total{model}`
- `llm_request_latency_seconds{model}`

**NL2SQL** — `app/services/nl2sql_service.py`：

- `nl2sql_queries_total`
- `nl2sql_query_errors_total`

**其它已埋点域（节选）**：RAG（`rag_*`）、Analysis / Agent（`analysis_*`）、小模型（`small_model_frames_processed_total`）、检修提取 / Trace 运维等，均集中于 `app/core/metrics.py`。

### 2.3 临时验收（无需 Prometheus）

```bash
curl -s "http://127.0.0.1:${APP_PORT:-8083}/metrics" | head
curl -s "http://127.0.0.1:8000/metrics" | head   # vLLM
```

---

## 3. 目标架构

```text
                    ┌─────────────────────────────┐
                    │  monitoring-deploy/（新建）   │
                    │  prometheus :9091             │
                    │  grafana    :3000             │
                    │  （可选）alertmanager :9093   │
                    └──────────────┬──────────────┘
           scrape（Docker DNS / 宿主机）
     ┌─────────────┼──────────────┬──────────────┐
     ▼             ▼              ▼              ▼
 models-app     vllm-service   （可选）        （二期）
 /metrics       /metrics       mineru 黑盒     node/NPU
```

| 组件 | 职责 |
|------|------|
| 应用 / vLLM | 只暴露 Prometheus 文本指标 |
| Prometheus | 拉取、存 TSDB、评估告警规则 |
| Grafana | 数据源 = Prometheus；Dashboard + 告警可视化 |
| Alertmanager（可选） | 邮件 / 企微 / 钉钉等通知 |

**原则**：监控栈独立于业务 compose；通过 Docker 外部网络或宿主机端口到达 scrape 目标。

---

## 4. 推荐交付形态：`monitoring-deploy/`

与 `mineru-deploy`、`rag_db-deploy` 同级，避免污染业务编排。

### 4.1 建议目录结构

```text
monitoring-deploy/
  README.md
  .env.example
  docker-compose.yml
  prometheus/
    prometheus.yml
    rules/
      analysis-trace-alert-rules.yml   # 可自 configs/monitoring 同步或挂载
      app-http-alerts.yml              # 新建：HTTP / LLM / NL2SQL / up
  grafana/
    provisioning/
      datasources/datasource.yml       # 自动挂 Prometheus
      dashboards/dashboards.yml
    dashboards/
      01-app-overview.json
      02-llm.json
      03-rag-nl2sql.json
      04-analysis.json
      05-host-resources.json
```

### 4.2 网络接入

Prometheus / Grafana 须能解析到目标容器，择一或组合：

1. **加入已有外部网络**（推荐）：如 `VLLM_DOCKER_NETWORK`（常见 `docker_vllm-network`）、应用栈所在网络（与 vLLM / RAG 互通时）。
2. **宿主机端口 scrape**（专网省事）：`host.docker.internal:8083`、`172.17.0.1:8083` 等（按宿主机实际网关调整）。

网络名必须以各栈 `.env` 为准，与 `app/app-deploy`、`vllm-deploy` 对齐。

### 4.3 持久化与昇腾现场路径建议

与现有存储规划一致（参见 `README-DEPLOY-ASCEND.md`）：

| 用途 | 建议宿主机路径 |
|------|----------------|
| Prometheus TSDB | `/aidata/data/prometheus` |
| Grafana 数据 | `/aidata/data/grafana` |
| 部署代码 / 配置 | `/opt/deploy/.../monitoring-deploy` |

保留策略建议：`retention 15d`～`30d`（按磁盘容量调整）。

### 4.4 与现有 vLLM `monitoring` profile 的关系

| 策略 | 说明 |
|------|------|
| **推荐** | 新建 `monitoring-deploy` 为权威监控栈；`vllm-deploy` 的 `profiles: monitoring` 在 README 中标记 **deprecated**，并指向本文档 |
| **过渡** | 短期可并存，但避免两套 Prometheus 重复采集、规则不一致 |
| **禁止长期** | 仅用 vLLM 内嵌 Prometheus 却期望覆盖 `models-app` 指标（需改配置且职责不清） |

---

## 5. Prometheus 采集配置（核心）

在现有 `vllm-deploy/docker/prometheus.yml` 思路上扩展为「全栈」配置，示意如下（实施时按真实容器名 / 端口改写）：

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/rules/*.yml

scrape_configs:
  - job_name: models-app
    metrics_path: /metrics
    static_configs:
      - targets: ["models-app:8080"]   # 容器内监听端口以 compose 为准；宿主机映射多为 8083
        labels:
          stack: "app"
          env: "prod"

  - job_name: vllm
    metrics_path: /metrics
    static_configs:
      - targets: ["vllm-service:8000"]
        labels:
          stack: "vllm"
          env: "prod"

  # 可选：models-app-gpu（若启用 small-model GPU 实例）
  # - job_name: models-app-gpu
  #   metrics_path: /metrics
  #   static_configs:
  #     - targets: ["models-app-gpu:8080"]
```

**实施注意**：

1. 完整实现 **必须** 增加 `models-app` job；仅抓 vLLM 不算闭环。
2. 若存在 `models-app-gpu`（宿主机常见 `APP_PORT_GPU=8081`），单独加 job 或使用 file-based service discovery。
3. `/metrics` 当前通常无鉴权：监控端口与业务端口应限制在管理网 / 内网；后续可按需加 basic auth 或仅集群内可达。
4. **高基数风险**：HTTP 中间件使用 `path=request.url.path`，若路径含动态 ID，可能导致时间序列爆炸。完整实现后期应评估改为路由模板（如 `/rag/docs/{id}` → `/rag/docs/:id`），属可选加固项，不阻塞 Phase 1。

---

## 6. Grafana 看板规划

| 阶段 | Dashboard | 关键面板（基于已有指标） |
|------|-----------|------------------------|
| P0 | 应用总览 | `rate(http_requests_total[5m])`、5xx 比例、延迟 P95 |
| P0 | LLM | `llm_requests_total`、`llm_request_latency_seconds` by `model` |
| P1 | RAG / NL2SQL | `rag_*`、`nl2sql_queries_total` / `nl2sql_query_errors_total` |
| P1 | Analysis | `analysis_requests_total`、`analysis_node_latency_seconds`、`analysis_degrade_total` |
| P2 | Trace 运维 | 复用 `analysis-trace-alert-rules.yml` 中 recording rules |
| P2 | 小模型 | `small_model_frames_processed_total` |

PromQL 细节可参考 `docs/大小模型应用技术架构与实现方案.md` §5.2。

**Grafana 配置约定**：

- 默认数据源：`http://prometheus:9091`
- admin 密码写入 `monitoring-deploy/.env`，禁止写死进镜像
- Dashboard 使用 **provisioning 自动加载**，避免手工导入后丢失

---

## 7. 告警规则

### 7.1 已有（直接挂载）

- `configs/monitoring/analysis-trace-alert-rules.yml`  
  （仓库内 `vllm-deploy/docker/analysis-trace-alert-rules.yml` 为同源副本时可择一同步，避免双份漂移）

### 7.2 建议新增（P0）

| 告警 | 意图 |
|------|------|
| `up{job="models-app"} == 0` | 应用 scrape 失败 / 进程不可达 |
| `up{job="vllm"} == 0` | vLLM scrape 失败 |
| HTTP 5xx 率过高 | 针对 chatbot / analysis / nl2sql 等关键 path |
| LLM 延迟 P95 过高 | `llm_request_latency_seconds` |
| NL2SQL 错误率过高 | `nl2sql_query_errors_total / nl2sql_queries_total` |

文档中的告警 YAML 样例见 `docs/大小模型应用技术架构与实现方案.md` §5.1。

### 7.3 通知通道

| 阶段 | 方式 |
|------|------|
| 一期 | Grafana 统一联系点（邮件 / Webhook） |
| 二期 | Alertmanager → 企微 / 钉钉 / 邮件 |

---

## 8. 安全与运维约定

1. **指标与监控 UI 仅内网**：宿主机 `9091` / `3000` 做防火墙或仅管理网开放。
2. **`/metrics` 鉴权策略**与现网 Service API Key 策略对齐；若暂不鉴权，Prometheus 与 Grafana **禁止**公网暴露。
3. **启动顺序**：业务栈（EasySearch → vLLM → 可选 MinerU → app）先起 → `monitoring-deploy`；停栈相反。
4. **专网 / 离线**：`prom/prometheus`、`grafana/grafana` 镜像在有网机构建或拉取后 `docker save` / `load`；配置与数据目录按 §4.3 落盘。
5. **验收写入部署手册**：`README-DEPLOY-ASCEND.md`、`app/app-deploy/README.md` 验收表中「Prometheus 抓取 `/metrics`（若接入）」在落地后改为可执行步骤。

---

## 9. 实施阶段与清单

> **落地状态（仓库）**：Phase 0～3 交付物已合入 `monitoring-deploy/`；现场需按 README 启动并验收 Targets=UP。

### Phase 0 — 验证基线（约 0.5 天）

- [x] `curl` 应用 `/metrics`、vLLM `/metrics` 有数据（脚本：`monitoring-deploy/scripts/check-baseline.sh` / `.ps1`）
- [x] 确认容器名、容器内端口、Docker 网络名（与各栈 `.env` 一致；见 `monitoring-deploy/.env.example`）
- [x] 确认宿主机映射：`APP_PORT`（默认 8083）、`VLLM_PORT`（默认 8000）

### Phase 1 — 最小闭环 MVP（约 2–3 天）**【完整实现的必达】**

- [x] 新建 `monitoring-deploy/`（compose + `prometheus.yml` + Grafana provisioning）
- [x] scrape：`models-app` + `vllm-service`
- [x] 挂载 analysis-trace 规则 + 基础 `up` / HTTP 告警
- [x] Grafana：1 个总览 + 1 个 LLM 看板（`01-app-overview` / `02-llm`）
- [x] 编写 `monitoring-deploy/README.md`
- [x] `README-DEPLOY-ASCEND.md` 增加「监控（可选）」章节与启停命令
- [x] **验收**：Prometheus Targets = UP；Grafana 可见近 15 分钟曲线（现场执行）

### Phase 2 — 业务看板与告警（约 2–3 天）

- [x] RAG / NL2SQL / Analysis Dashboard（`03-rag-nl2sql` / `04-analysis`）
- [x] LLM / NL2SQL 等业务告警规则落地（`app-business-alerts.yml`）
- [x] Grafana 联系点或 Alertmanager 通知到运维群（Alertmanager 已部署；Webhook URL 需现场改成企微/钉钉）

### Phase 3 — 加固与扩展（按需）

- [x] HTTP `path` 高基数治理（路由模板化 + 动态段折叠：`app/core/metrics_path.py`）
- [x] MinerU / EasySearch 黑盒探测或各自 exporter（Blackbox 含 app/vllm/mineru；EasySearch 见 `prometheus/optional-easysearch.fragment.yml`）
- [x] NPU / 节点指标（**node-exporter 默认启动**；NPU exporter 说明见 `monitoring-deploy/README.md` §7）
- [x] 下线或冻结 `vllm-deploy` 内嵌 Prometheus profile，统一指向 `monitoring-deploy`（README + compose 标注 Deprecated）

---

## 10. 验收标准（Definition of Done）

1. Prometheus **Targets** 中 `models-app`、`vllm` 状态为 **UP**。
2. Grafana 能查询到 `http_requests_total`、`llm_requests_total` 近 15 分钟曲线。
3. 人为停止应用容器后，`up{job="models-app"} == 0` 可触发告警（Prometheus / Grafana）。
4. 专网场景：镜像可离线 `load`；数据目录在 `/aidata/data/...`；部署手册含启停与验收命令。
5. 业务功能路径无强制依赖监控栈（监控挂掉不影响推理 / RAG / NL2SQL）。

---

## 11. 启动顺序（落地后建议）

```text
rag_db-deploy
  → vllm-deploy
  → （可选）mineru-deploy
  → （可选）paddleocr-layout-deploy
  → app/app-deploy
  → monitoring-deploy     # 最后：依赖业务 /metrics 已可访问
```

示意命令（实施后以 `monitoring-deploy/README.md` 为准）：

```bash
cd monitoring-deploy
cp .env.example .env
# 编辑网络名、数据目录、Grafana 密码
docker compose --env-file .env up -d
```

验证：

```bash
# Prometheus UI
curl -s "http://127.0.0.1:9091/-/ready"

# Targets（浏览器：http://127.0.0.1:9091/targets）
# Grafana：http://127.0.0.1:3000 （默认数据源 Prometheus）
```

---

## 12. 与认知对齐（摘要）

| 理解 | 结论 |
|------|------|
| 目的是监控应用相关指标 | **正确**；扩展采集范围为 app + vLLM（+ 可选其它） |
| 应用侧已实现指标打点 | **正确**；完整实现时基本不改业务代码 |
| 指标尚未被监控体系真正「用起来」 | **默认部署路径下正确**（仅进程内累计 + 可选仅抓 vLLM） |
| 还需部署 Prometheus + Grafana | **正确**；推荐独立 **`monitoring-deploy/`** |
| Prometheus 采集、Grafana 展示 | **正确**；告警规则放 Prometheus，通知可用 Grafana / Alertmanager |

**一句话**：打点已在应用多处落地；完整「用起来」需要独立监控栈、把 `models-app` 纳入 scrape、交付看板与告警，并写入部署验收文档。

---

## 13. 实施状态

| 项 | 状态 |
|----|------|
| 应用 `/metrics` 与 `app/core/metrics.py` | **已有** |
| HTTP path 高基数治理 | **已有**（`app/core/metrics_path.py`） |
| vLLM 可选 Prometheus（仅 vLLM） | **已废弃**（改用 `monitoring-deploy/`） |
| `monitoring-deploy/` 独立栈 | **已落地** |
| Grafana 看板与 provisioning | **已落地**（5 个看板，含主机资源） |
| 全栈 scrape + 告警接线 | **已落地**（Webhook 需现场配置） |
| 部署手册监控章节 | **已落地**（`README-DEPLOY-ASCEND.md` §6.5） |

> 运维细则与启停命令以 [`monitoring-deploy/README.md`](../monitoring-deploy/README.md) 为准。
