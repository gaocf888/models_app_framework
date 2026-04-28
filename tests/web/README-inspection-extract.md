# tests/web 检修报告结构化提取页面说明

本目录新增调试页面：`inspection-extract.html`，用于浏览器侧联调以下接口：

- `POST /inspection-extract/upload`：上传本地文件到 MinIO
- `POST /inspection-extract/run`：基于上传后的 URL 执行结构化提取

## 1. 前置条件

- 后端服务已启动，可访问 `http://<host>:<port>/inspection-extract/*`
- 服务端已配置 MinIO（`upload` 接口会写入对象存储）
- 已准备 Service API Key（若服务端开启鉴权）
- 浏览器建议使用 Chrome / Edge / Firefox

## 2. 启动页面

建议通过本地静态服务访问，不要直接使用 `file://`：

```bash
cd tests/web
python3 -m http.server 8765
```

浏览器打开：

- [http://127.0.0.1:8765/inspection-extract.html](http://127.0.0.1:8765/inspection-extract.html)

## 3. 页面功能

- **连接与鉴权**
  - `API 根地址`：例如 `http://127.0.0.1:8083`
  - `Service API Key`：填写纯密钥，页面自动加 `Bearer `
- **上传文件**
  - 选择本地报告文件（doc/docx/pdf/md/txt）
  - 点击“1) 上传文件”调用 `/inspection-extract/upload`
  - 点击“将上传结果填充到 run 参数”，自动写入 `source_type` 与 `content(url)`
- **执行提取**
  - 点击“2) 执行结构化提取”调用 `/inspection-extract/run`
  - 页面展示完整 JSON 响应

## 4. 推荐联调流程

1. 填写 `API 根地址`、`Service API Key`
2. 选择本地报告文件并执行上传
3. 点击“将上传结果填充到 run 参数”
4. 根据需要设置 `strict`、`return_evidence`、`prompt_version`
5. 执行提取并查看 `run 响应`

## 5. 请求体字段说明（run）

- `user_id`：用户标识
- `session_id`：会话标识
- `source_type`：文档类型（如 `docx`/`pdf`/`markdown`/`text`）
- `content`：文档内容或 URL（生产建议传 `upload` 返回的 MinIO URL）
- `strict`：严格模式（可选）
- `return_evidence`：是否返回证据字段（可选）
- `prompt_version`：提示词版本（可选）

## 6. 常见问题

- **401/403**：Service API Key 缺失或错误
- **400 empty file upload**：未选择文件或文件内容为空
- **422**：`run` 请求字段不符合后端模型校验
- **跨域错误**：后端 CORS 未放开当前页面来源
- **upload 成功但 run 失败**：检查 MinIO URL 是否过期、后端是否可访问该 URL

## 7. 安全提示

- 不要在不可信环境输入生产密钥
- 不要将包含真实密钥或真实业务数据的截图/日志外发

