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
| [analysis-img-diag.html](analysis-img-diag.html) | 综合分析 **看图诊断**：`POST /analysis/img-diag/upload` + `POST /analysis/run-img-diag` |

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

## 4. `analysis-img-diag.html`（综合分析 · 看图诊断）

### 4.1 前置条件

- `POST /analysis/img-diag/upload`、`POST /analysis/run-img-diag` 可用  
- MinIO（或与上传接口一致的对象存储）已配置  
- 多模态 / 视觉模型与 NL2SQL、RAG 等依赖按部署文档就绪  

### 4.2 访问示例

[http://127.0.0.1:8765/analysis-img-diag.html](http://127.0.0.1:8765/analysis-img-diag.html)

### 4.3 推荐流程

1. 填写 API 根地址与 Service API Key、`user_id`、`session_id`  
2. （可选）选择 jpeg/png/webp → 「1) 上传图片」→ 「将上传 URL 追加到 image_urls」（可多次上传多图）  
3. 填写 **unit_id**、**leak_location_text**、**query**；按需编辑 **leak_location_struct**（JSON 对象）  
4. **image_urls** 至少一行（预签名 URL）  
5. 「2) 执行看图诊断」→ 查看 JSON（含 `evidence.vision_findings`、`parallel_lane_trace` 等）

### 4.4 请求体要点（与后端 `AnalysisImgDiagRequest` 对齐）

- **必填**：`user_id`、`session_id`、`unit_id`、`leak_location_text`、`query`、`image_urls`  
- **可选**：`leak_location_struct`（默认 `{}`）、`data_requirements_hint`、`options`（页面提供 `enable_rag`、`enable_context`、`strict`、`max_nl2sql_calls`）

### 4.5 延伸阅读

- `enterprise-level_transformation_docs/企业级综合分析-看图诊断实现和使用说明.md`  
- `framework-guide/综合分析整体实现技术说明.md`

---

## 5. 通用常见问题与安全

- **401/403**：密钥错误或未填（而后端已开鉴权）  
- **422**：`user_id` / `session_id` 等不符合后端校验规则  
- **跨域**：后端 CORS 需允许静态页来源  

**安全**：勿在不可信环境输入生产密钥；勿外泄含真实业务数据的响应与截图。
