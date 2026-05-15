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

| 页面 | 用途 |
|------|------|
| [chatbot-stream.html](chatbot-stream.html) | 智能客服：`POST /chatbot/chat/stream`（SSE） |
| [inspection-extract.html](inspection-extract.html) | 检修提取 **同步**：`upload` + `POST /inspection-extract/run` |
| [inspection-extract-async.html](inspection-extract-async.html) | 检修提取 **异步**：`run/async` + 任务轮询与分块 |
| [inspection-extract-v0-async.html](inspection-extract-v0-async.html) | 检修提取 **V0 异步**（LangGraph + 版面 OCR）：`/inspection-extract-v0/*` |
| [analysis-img-diag.html](analysis-img-diag-stream.html) | 综合分析 **看图诊断（流式）**：`POST /analysis/img-diag/upload` + `POST /analysis/run-img-diag-stream`（SSE，与超温流式页同构） |
| [analysis-nl2sql-stream.html](analysis-nl2sql-stream.html) | 综合分析 **NL2SQL 流式 synthesis（全专项）**：超温 / 检修策略 / 四管健康解读 / 泄爆 · `POST /analysis/run-with-nl2sql-stream` |
| [analysis-nl2sql-overheat-stream.html](analysis-nl2sql-overheat-stream.html) | 综合分析 **NL2SQL 流式 synthesis（超温专项页，保留）**：`POST /analysis/run-with-nl2sql-stream` |

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
3. 填写 **unit_id**、**leak_location_text**、**query**；按需编辑 **leak_location_struct**（JSON 对象）  
4. **image_urls** 至少一行（预签名 URL）  
5. 「2) 开始流式看图诊断」→ 区段内 **summary 增量**、`meta` / 事件 trace；完整 JSON 见服务端日志与 `GET /analysis/traces/{request_id}`（`request_id` 见首包 `meta`）

### 4.4 请求体要点（与后端 `AnalysisImgDiagRequest` 对齐）

- **必填**：`user_id`、`session_id`、`unit_id`、`leak_location_text`、`query`、`image_urls`  
- **可选**：`leak_location_struct`（默认 `{}`）、`data_requirements_hint`、`options`（页面提供 `enable_rag`、`enable_context`、`strict`、`max_nl2sql_calls`）

### 4.5 延伸阅读

- `enterprise-level_transformation_docs/企业级综合分析-看图诊断实现和使用说明.md`  
- `framework-guide/综合分析整体实现技术说明.md`

---

## 5. `analysis-nl2sql-stream.html`（综合分析 · 全专项 NL2SQL 流式 synthesis）

### 5.1 前置条件

- `POST /analysis/run-with-nl2sql-stream` 已部署；vLLM 流式与 NL2SQL 服务可用  
- 页面支持 **`analysis_type`**：`overheat_guidance`（超温）、`maintenance_strategy`（检修策略）、`four_tube_health_interpretation`（四管健康解读）、`leakage_burst_analysis`（泄爆）  

### 5.2 访问示例

[http://127.0.0.1:8765/analysis-nl2sql-stream.html](http://127.0.0.1:8765/analysis-nl2sql-stream.html)

### 5.3 行为说明

- 切换专项后可点「填入当前专项示例 query」快速联调  
- **取数、质量门、RAG** 阶段仍阻塞在首包之前  
- SSE 事件顺序一般为：`meta` → 多条 `summary_delta` → `summary_complete` → `structured_async_enqueued`  
- 完整 `AnalysisV2Result` 异步落日志与 trace；可用 `GET /analysis/traces/{request_id}` 查询  

### 5.4 与超温专用页关系

- [analysis-nl2sql-overheat-stream.html](analysis-nl2sql-overheat-stream.html) 保留，默认聚焦超温演示；全专项请用本页。  

---

## 6. `analysis-nl2sql-overheat-stream.html`（综合分析 · 超温 NL2SQL 流式 synthesis）

### 6.1 前置条件

- 同 §5.1；本页默认 **`analysis_type=overheat_guidance`**，亦可在下拉中切换 `maintenance_strategy` / `custom`  

### 6.2 访问示例

[http://127.0.0.1:8765/analysis-nl2sql-overheat-stream.html](http://127.0.0.1:8765/analysis-nl2sql-overheat-stream.html)

### 6.3 行为说明

- 与 §5.3 相同（流式 synthesis、异步 structured_report）  

### 6.4 与同步接口差异（提示）

- 流式路由走 **顺序管道** 实现；若生产上同步接口走 LangGraph 且需行为逐字节一致，请用 `POST /analysis/run-with-nl2sql` 对照。  

---

## 7. 通用常见问题与安全

- **401/403**：密钥错误或未填（而后端已开鉴权）  
- **422**：`user_id` / `session_id` 等不符合后端校验规则  
- **跨域**：后端 CORS 需允许静态页来源  

**安全**：勿在不可信环境输入生产密钥；勿外泄含真实业务数据的响应与截图。
