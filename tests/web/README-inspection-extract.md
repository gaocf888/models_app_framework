# tests/web 检修报告结构化提取页面说明

本目录提供两个调试页面，用于浏览器联调检修提取相关接口（均需 `Authorization: Bearer <SERVICE_API_KEY>`，若服务端关闭鉴权可留空）：

| 页面 | 用途 |
|------|------|
| [inspection-extract.html](inspection-extract.html) | **同步**：`POST /inspection-extract/run`，一次请求等待完整结果（长耗时时可能因浏览器/网络超时失败） |
| [inspection-extract-async.html](inspection-extract-async.html) | **异步**：`POST /inspection-extract/run/async` 得 `job_id`，短轮询 `GET /inspection-extract/jobs/{id}`，并可查看分块 `.../chunks`、`.../chunks/{work_idx}` |

## 1. 前置条件

- 后端已启动，可访问 `http://<host>:<port>/inspection-extract/*`
- 已配置 MinIO（`upload` 会写入对象存储）
- 已准备 Service API Key（若开启鉴权）
- 建议使用 Chrome / Edge / Firefox

## 2. 启动本地静态服务

勿使用 `file://` 打开 HTML，请用 HTTP 服务（与 CORS、相对路径一致）：

```bash
cd tests/web
python3 -m http.server 8765
```

浏览器打开：

- 同步页：[http://127.0.0.1:8765/inspection-extract.html](http://127.0.0.1:8765/inspection-extract.html)
- 异步页：[http://127.0.0.1:8765/inspection-extract-async.html](http://127.0.0.1:8765/inspection-extract-async.html)

## 3. 同步页 `inspection-extract.html`

### 3.1 功能

- **连接与鉴权**：`API 根地址`（如 `http://127.0.0.1:8083`）、`Service API Key`（仅填密钥，页面加 `Bearer `）
- **上传**：`POST /inspection-extract/upload`，可将返回的 `url`、`source_type` 一键填入 run 参数
- **执行**：`POST /inspection-extract/run`，展示完整 JSON

### 3.2 推荐流程

1. 填写 API 地址与密钥  
2. 选择本地文件 → 「1) 上传文件」  
3. 「将上传结果填充到 run 参数」  
4. 按需勾选 `strict`、`return_evidence`、`prompt_version`  
5. 「2) 执行结构化提取」→ 查看 **run 响应**

### 3.3 run 请求体字段

- `user_id`、`session_id`
- `source_type`：如 `docx` / `pdf` / `markdown` / `text`
- `content`：建议为 upload 返回的 MinIO URL
- `strict`、`return_evidence`、`prompt_version`（可选）

---

## 4. 异步页 `inspection-extract-async.html`

适用于解析耗时较长、需避免单次 HTTP 长时间挂起的场景：提交后立即返回 `job_id`，由页面按间隔轮询任务状态；各含表分块 parse 完成后可单独拉取，避免只依赖终态大 JSON。

### 4.1 调用的接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/inspection-extract/upload` | 与同步页相同 |
| POST | `/inspection-extract/run/async` |  body 与 `/run` 相同，返回 `job_id`、`job_status_path` |
| GET | `/inspection-extract/jobs/{job_id}?include_result=false` | 轮询用；默认不拉取终态大 `result` |
| GET | `/inspection-extract/jobs/{job_id}/chunks` | 各分块 `work_idx`、是否已落盘、条数 |
| GET | `/inspection-extract/jobs/{job_id}/chunks/{work_idx}` | 单块 parse 结果（未落盘为 404） |

### 4.2 页面行为

- 填写 API、密钥、会话、content 等与同步页类似  
- 「2) 提交异步任务」→ 自动以设定间隔（默认 2000ms）轮询任务状态，至 `completed` 或 `failed` 后停止  
- 「手动拉取任务状态」：不依赖轮询，仅请求一次  
- 「刷新分块列表」：调用 `.../chunks`  
- 输入 `work_idx` + 「加载该块」：调用 `.../chunks/{work_idx}`  
- **恢复会话**：将历史 `job_id` 粘贴到「手动粘贴 job_id」→「使用该 ID 并开始轮询」（利于刷新页面后继续看进度）  
- 任务完成后可点「刷新分块列表」查看各块；分块内容较大时仍建议按块查看，避免一次加载整单

### 4.3 与后端的说明

- 异步任务状态与分块文件落在服务端配置的目录（如 `INSPECT_EXTRACT_ASYNC_JOBS_DIR`）；`REDIS_URL` 仅影响多实例下任务队列，与页面无关。  
- 轮询间隔过短会增加服务端压力，建议 2s 起。

---

## 5. 常见问题

- **401/403**：Service API Key 错误或未填（而服务端已开鉴权）  
- **400 empty file upload**：未选文件或文件为空  
- **422**：`user_id` / `session_id` 等不符合后端校验  
- **跨域**：后端 CORS 需允许当前页面来源（如 `http://127.0.0.1:8765`）  
- **同步页长时间「执行失败」而后端实际成功**：多为浏览器或链路对长请求超时；可改用 **异步页**  
- **upload 成功但 run 失败**：MinIO 预签名过期、或后端无法访问该 URL  

## 6. 安全提示

- 勿在不可信环境输入生产密钥  
- 勿外泄含真实业务数据的截图与日志  
