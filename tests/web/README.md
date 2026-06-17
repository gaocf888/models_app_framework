# tests/web 浏览器调试页面说明

本目录提供若干 **静态 HTML 调试页**，用于本地联调后端 HTTP 接口。均需按需携带  
`Authorization: Bearer <SERVICE_API_KEY>`（密钥为空仅适用于服务端关闭鉴权的环境）。

**勿使用 `file://` 打开**，请在本目录启动简易 HTTP 服务后再访问：

```bash
cd tests/web
python3 -m http.server 8765
# Windows 亦可：python -m http.server 8765
```

---

## 页面索引

| 页面 | 用途                                                                                                                |
|------|-------------------------------------------------------------------------------------------------------------------|
| [chatbot-stream.html](chatbot-stream.html) | 智能客服：`POST /chatbot/chat/stream`（SSE）                                                                             |
| [inspection-extract.html](inspection-extract.html) | 检修提取 **同步**：`upload` + `POST /inspection-extract/run`                                                             |
| [inspection-extract-async.html](inspection-extract-async.html) | 检修提取 **异步**：`run/async` + 任务轮询与分块（当前在用版本）                                                                         |
| [inspection-extract-v0-async.html](inspection-extract-v0-async.html) | 检修提取 **V0 异步**（LangGraph + 版面 OCR）：`/inspection-extract-v0/*`                                                     |
| [analysis-img-diag.html](analysis-img-diag-stream.html) | 综合分析 **看图诊断-泄爆分析/缺陷识别（流式）**：`POST /analysis/img-diag/upload` + `POST /analysis/run-img-diag-stream`（SSE，与超温流式页同构） |
| [analysis-nl2sql-stream-v1.html](analysis-nl2sql-stream-v1.html) | 综合分析 **NL2SQL 流式 synthesis v1（全专项，默认策略）**：`POST /analysis/run-with-nl2sql-stream`                                 |
| [analysis-nl2sql-overheat-stream-v1.html](analysis-nl2sql-overheat-stream-v1.html) | 综合分析 **NL2SQL 流式 synthesis v1（超温专项页）**                                                                            |
| [analysis-nl2sql-stream-v2.html](analysis-nl2sql-stream-v2.html) | 综合分析 **NL2SQL 流式 synthesis v2（超温多槽位；需服务端 env）**：同上接口，可收 `table_payload` / `chart_payload`                         |
| [analysis-agent-stream.html](analysis-agent-stream.html) | **综合分析智能体** `POST /analysis-agent/run-stream`（按章 SSE、HITL resume、ECharts）                                         |

---

## 1. `chatbot-stream.html`

### 1.1 前置条件

- 后端可访问 `http://<host>:<port>/chatbot/chat/stream`
- Service API Key（若开启鉴权）

### 1.2 访问示例

[http://127.0.0.1:8765/chatbot-stream.html](http://127.0.0.1:8765/chatbot-stream.html)

### 1.3 字段说明

- **API 根地址**：如 `http://127.0.0.1:8083`
- **Service API Key**：仅填密钥，页面自动加 `Bearer `
- **user_id / session_id**：会话标识（勿包含非法字符，参见后端校验）
- **query**：本轮问题
- **enable_rag / enable_context / enable_nl2sql_route**
- **prompt_version**：可选
- **enable_fault_vision**：可选，默认不传
- **image_urls**：每行一个 URL

### 1.4 操作流程

1. 填写连接与会话参数，输入 `query`  
2. 「发送并开始流式输出」→ 先收到 `started`（含 `stream_id`），再持续 `delta`  
3. 结束见 `finished.meta`；可按需「中断」  
4. 回答区对模型输出的 **LaTeX 公式**（`$$...$$`、`\\(...\\)`、`\\[...\\]`）使用 KaTeX 渲染；**Markdown 标题/列表等保持原文**，不转 HTML（需页面能访问 jsDelivr CDN；请 **Ctrl+F5** 强刷缓存后再测）。

### 1.5 常见问题

- **401/403**：密钥错误或缺失  
- **422**：ID 或字段校验失败  
- **无 SSE**：检查网关是否缓冲 `text/event-stream`  
- **跨域**：后端 CORS 需放行页面来源（如 `http://127.0.0.1:8765`）

---

## 2. `inspection-extract.html`（同步）

### 2.1 前置条件

- `POST /inspection-extract/*` 可用；MinIO 已配置（upload 写入对象存储）

### 2.2 访问示例

[http://127.0.0.1:8765/inspection-extract.html](http://127.0.0.1:8765/inspection-extract.html)

### 2.3 推荐流程

1. 填写 API 地址与密钥  
2. 选择本地文件 → 「1) 上传文件」  
3. 「将上传结果填充到 run 参数」  
4. 「2) 执行结构化提取」→ 查看 **run 响应**

### 2.4 `run` 请求体要点

- `user_id`、`session_id`  
- `source_type`、`content`（建议为 upload 返回的 URL）  
- `strict`、`return_evidence`、`prompt_version`（可选）

### 2.5 说明

长耗时单次 HTTP 可能被浏览器或链路超时；大文档建议改用 **异步页**。

---

## 3. `inspection-extract-async.html`（异步）

### 3.1 访问示例

[http://127.0.0.1:8765/inspection-extract-async.html](http://127.0.0.1:8765/inspection-extract-async.html)

### 3.2 接口速查

| 方法 | 路径 |
|------|------|
| POST | `/inspection-extract/upload` |
| POST | `/inspection-extract/run/async` |
| GET | `/inspection-extract/jobs/{job_id}` |
| GET | `/inspection-extract/jobs/{job_id}/chunks` |
| GET | `/inspection-extract/jobs/{job_id}/chunks/{work_idx}` |

### 3.3 行为摘要

提交异步任务后轮询状态至 `completed` / `failed`；可按 `work_idx` 拉取分块 parse 结果，避免一次加载超大 JSON。

轮询间隔建议 ≥ 2s，减轻服务端压力。

---

## 3.5 `inspection-extract-v0-async.html`（检修 V0 · 异步）

### 3.5.1 前置条件

- 后端 **`INSPECT_EXTRACT_V0_ENABLED=true`** 并已重启（未开启时请求返回 **503** + `detail`，不再误报 404）
- MinIO 与现网一致（`POST …/upload`）
- 版面 OCR 侧车、LLM 等按 V0 部署文档就绪

### 3.5.2 访问示例

[http://127.0.0.1:8765/inspection-extract-v0-async.html](http://127.0.0.1:8765/inspection-extract-v0-async.html)

### 3.5.3 接口速查

| 方法 | 路径 |
|------|------|
| POST | `/inspection-extract-v0/upload` |
| POST | `/inspection-extract-v0/run/async` |
| GET | `/inspection-extract-v0/jobs/{job_id}`（Query：`include_result`；为 `false` 时 `result` 为 `null`） |
| DELETE | `/inspection-extract-v0/jobs/{job_id}`（取消） |
| GET | `/inspection-extract-v0/jobs/{job_id}/chunks` |
| GET | `/inspection-extract-v0/jobs/{job_id}/chunks/{work_idx}` |

V0 单段异步任务 **`work_idx` 一般为 `1`**；`strict` 可不传，走 `INSPECT_EXTRACT_V0_STRICT_DEFAULT`。

### 3.5.4 与现网异步页差异

- 路径前缀 **`/inspection-extract-v0`**，Redis 队列前缀与现网隔离（`inspection:extract:v0:jobs`）
- 响应中含 V0 **trace / metrics**（版面引擎、解析路由等），详见 OpenAPI `/docs`
- **`GET …/jobs/{job_id}`**：`include_result=false`（默认轻量轮询）时 **`result` 字段为 `null` 是接口设计**；需完整结构化结果时请 `include_result=true`。静态页在任务 **`completed`** 后会自动再拉一次含 `result` 的响应。
- 若日志出现 **`langgraph invoke failed … unable to open database file`**：LangGraph 在任务目录下创建 SQLite checkpoint 失败时会 **回退顺序执行**，一般不影响最终结果；持久化排查请确认 `INSPECT_EXTRACT_ASYNC_JOBS_DIR` 所在卷对进程 **可写**、非只读挂载。

---

## 4. `analysis-img-diag.html`（综合分析 · 看图诊断）

### 4.1 前置条件

- `POST /analysis/img-diag/upload`、`POST /analysis/run-img-diag-stream` 可用（本页主流程为流式；同步 `run-img-diag` 可改用 API 客户端自测）  
- MinIO（或与上传接口一致的对象存储）已配置  
- 多模态 / 视觉模型与 NL2SQL、RAG 等依赖按部署文档就绪  

### 4.2 访问示例

[http://127.0.0.1:8765/analysis-img-diag.html](http://127.0.0.1:8765/analysis-img-diag.html)

### 4.3 推荐流程

1. 填写 API 根地址与 Service API Key、`user_id`、`session_id`  
2. （可选）选择 jpeg/png/webp → 「1) 上传图片」→ 「将上传 URL 追加到 image_urls」（可多次上传多图）  
3. 选择 **img_diag_subtype**：`defect_ident`（缺陷识别）或 `leakage_burst`（泄爆分析）
4. 填写 **query**（须含区域/管段位置；泄爆须含事故发生时间）；按需编辑 **data_requirements_hint**
5. **image_urls**：缺陷识别至少一行；泄爆分析可选（无图时视觉臂跳过）
6. 「2) 开始流式看图诊断」→ 区段内 **summary 增量**、`meta` / 事件 trace；完整 JSON 见服务端日志与 `GET /analysis/traces/{request_id}`（`request_id` 见首包 `meta`）

### 4.4 请求体要点（与后端 `AnalysisImgDiagRequest` 对齐）

- **必填**：`user_id`、`session_id`、`img_diag_subtype`、`query`
- **图片**：`defect_ident` 时 `image_urls` 至少一条；`leakage_burst` 可为空
- **可选**：`data_requirements_hint`、`options`（页面提供 `enable_rag`、`enable_context`、`strict`、`max_nl2sql_calls`）
- **位置/时间/范围**：写在 `query` 自然语言中，由 NL2SQL 基座解析；泄爆取 **事故锚点向前 3 天** 数据

### 4.5 延伸阅读

- `enterprise-level_transformation_docs/企业级综合分析-看图诊断实现和使用说明.md`  
- `framework-guide/综合分析整体实现技术说明.md`

---

## 5. `analysis-nl2sql-stream-v1.html`（综合分析 · 全专项 NL2SQL 流式 synthesis **v1**）

### 5.1 前置条件

- `POST /analysis/run-with-nl2sql-stream` 已部署；vLLM 流式与 NL2SQL 服务可用  
- **synthesis v1（默认）**：单次 LLM 生成整篇报告；服务端 `ANALYSIS_SYNTHESIS_STRATEGY` 默认为 `v1`  
- 页面支持 **`analysis_type`**：`overheat_guidance`、`maintenance_strategy`、`four_tube_health_interpretation`、`leakage_burst_analysis`  

### 5.2 访问示例

[http://127.0.0.1:8765/analysis-nl2sql-stream-v1.html](http://127.0.0.1:8765/analysis-nl2sql-stream-v1.html)

### 5.3 行为说明

- 切换专项后可点「填入当前专项示例 query」快速联调  
- **取数、质量门、RAG** 阶段仍阻塞在首包之前  
- SSE：`meta` → 多条 `summary_delta` → `summary_complete` → `structured_async_enqueued` → `{"finished":true,"meta":{...}}`（尾帧，与 AI 问答同形）  
- 完整 `AnalysisV2Result` 异步落日志与 trace；`GET /analysis/traces/{request_id}`  

### 5.4 相关页面

- 超温 v1 精简页：[analysis-nl2sql-overheat-stream-v1.html](analysis-nl2sql-overheat-stream-v1.html)  
- 超温 v2 多槽位页：[analysis-nl2sql-stream-v2.html](analysis-nl2sql-stream-v2.html)（需服务端 env，见 §7）  

---

## 6. `analysis-nl2sql-overheat-stream-v1.html`（综合分析 · 超温 NL2SQL 流式 synthesis **v1**）

### 6.1 前置条件

- 同 §5.1；默认 **`analysis_type=overheat_guidance`**  

### 6.2 访问示例

[http://127.0.0.1:8765/analysis-nl2sql-overheat-stream-v1.html](http://127.0.0.1:8765/analysis-nl2sql-overheat-stream-v1.html)

### 6.3 行为说明

- 与 §5.3 相同（v1 单次 LLM 流式 synthesis）  

### 6.4 与同步接口差异（提示）

- 流式路由走 **顺序管道** 实现；对照同步接口可用 `POST /analysis/run-with-nl2sql`  

---

## 7. `analysis-nl2sql-stream-v2.html`（综合分析 · 超温 NL2SQL 流式 synthesis **v2**）

### 7.1 前置条件

- 同 §5.1，且服务端已配置 **effective v2**（请求体不能切换策略），例如：  
  - `ANALYSIS_SYNTHESIS_STRATEGY_OVERHEAT_GUIDANCE=v2`（推荐）或全局 `ANALYSIS_SYNTHESIS_STRATEGY=v2`  
  - 建议 `ANALYSIS_PLAN_TEMPLATE_VERSION_OVERHEAT_GUIDANCE=v2`  
  - 可选 `ANALYSIS_SYNTHESIS_V2_ENABLE_STRUCTURED_SSE=true`（默认 true）以在流中收到 `table_payload` / `chart_payload`  
- **P0 仅 `overheat_guidance` 注册 v2 槽位**；其它专项配置 v2 会因无注册表回退 v1  

### 7.2 访问示例

[http://127.0.0.1:8765/analysis-nl2sql-stream-v2.html](http://127.0.0.1:8765/analysis-nl2sql-stream-v2.html)

### 7.3 行为说明

- 首帧 `meta.template_versions` 应含 `synthesis_strategy_effective: v2`；页面会提示若非 v2  
- SSE 除 `summary_delta` 外可有 `table_payload`、`chart_payload`（含 `slot_id`）  
- **plan v2**：超温 **q1～q6**（默认 `max_nl2sql_calls=6`）；**14 槽**（程序表/图 + 多段 LLM）；LLM 槽 prompt = `analysis_synthesis_overheat_narrative` + 槽位 `narrative_instruction`（非整篇 `analysis_synthesis_overheat_guidance` v2）  
- v1 对照：[analysis-nl2sql-stream-v1.html](analysis-nl2sql-stream-v1.html)、[analysis-nl2sql-overheat-stream-v1.html](analysis-nl2sql-overheat-stream-v1.html)  
- 设计说明：`docs/综合分析优化版本实现方案(v2版本).md`；槽位/q 映射：`enterprise-level_transformation_docs/系统整体技术实现-简版.md` §1.1.6  

---

## 8. 通用常见问题与安全

- **401/403**：密钥错误或未填（而后端已开鉴权）  
- **422**：`user_id` / `session_id` 等不符合后端校验规则  
- **跨域**：后端 CORS 需允许静态页来源  

**安全**：勿在不可信环境输入生产密钥；勿外泄含真实业务数据的响应与截图。
