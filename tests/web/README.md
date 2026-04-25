# tests/web 使用说明

本目录提供浏览器侧调试页面：`chatbot-stream.html`，用于调用并观察
`POST /chatbot/chat/stream` 的 SSE 流式输出。

## 1. 前置条件

- 后端服务已启动，并可访问 `http://<host>:<port>/chatbot/chat/stream`
- 已准备可用的 Service API Key（若后端开启了接口鉴权）
- 推荐使用现代浏览器（Chrome / Edge / Firefox）

## 2. 启动页面（推荐）

不要直接双击用 `file://` 打开，建议先起一个本地静态服务：

```bash
cd tests/web
python3 -m http.server 8765
```

浏览器访问：

- [http://127.0.0.1:8765/chatbot-stream.html](http://127.0.0.1:8765/chatbot-stream.html)

## 3. 页面字段说明

- `API 根地址`：例如 `http://127.0.0.1:8083`
- `Service API Key`：填写纯密钥值（页面会自动加 `Bearer ` 前缀）
- `user_id`、`session_id`：会话标识，建议不要包含 `:`
- `query`：本轮问题
- `enable_rag`：是否走 RAG 检索
- `enable_context`：是否带会话历史
- `enable_nl2sql_route`：是否允许路由到 NL2SQL
- `prompt_version`：可选，留空用服务端默认
- `enable_fault_vision`：可选，默认不传
- `image_urls`：每行一个 URL

## 4. 操作流程

1. 填写连接参数和会话参数
2. 输入 `query`
3. 点击“发送并开始流式输出”
4. 页面先收到 `started`（包含 `stream_id`），随后持续展示 `delta`
5. 请求结束后显示 `finished.meta`
6. 如需中断，可点击“中断（需 stream_id）”

## 5. 常见问题

- **401/403**：Service API Key 错误或缺失
- **422**：`user_id` / `session_id` / 请求体字段不符合后端校验
- **无流式输出**：检查后端是否真返回 `text/event-stream`，以及网关是否缓冲了 SSE
- **跨域报错**：确认后端 CORS 配置允许当前页面来源
- **长时间才出现首个 token**：通常是检索、重排、首次模型加载或 vLLM 首包延迟，不一定是前端问题

## 6. 安全提示

- 测试页面会在浏览器内存中保留你输入的 Key，不要在不可信机器使用
- 不要将真实生产密钥提交到仓库或截图外发
