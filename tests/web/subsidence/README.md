# tests/web/subsidence

地降所侧栏能力的浏览器联调页。勿用 `file://`，在 `tests/web` 起静态服务后访问：

```bash
cd tests/web
python -m http.server 8765
```

| 页面 | 用途 | 主接口 |
|------|------|--------|
| [intelligent-qa.html](intelligent-qa.html) | **智能问答** | `POST /chatbot/chat/stream` + stop / sessions |
| [auto-report.html](auto-report.html) | **自动报告生成** | `POST /analysis-agent/run-stream` + stream/stop |
| [data-query.html](data-query.html) | **数据查询** | `GET /data-query-agent/tree` + browse；`POST /run-stream` + resume HITL |
| [knowledge-base.html](knowledge-base.html) | **知识库** | `GET /rag/namespaces` + overview；upload / ingest / jobs |

打开示例：

- [http://127.0.0.1:8765/subsidence/intelligent-qa.html](http://127.0.0.1:8765/subsidence/intelligent-qa.html)
- [http://127.0.0.1:8765/subsidence/auto-report.html](http://127.0.0.1:8765/subsidence/auto-report.html)
- [http://127.0.0.1:8765/subsidence/data-query.html](http://127.0.0.1:8765/subsidence/data-query.html)
- [http://127.0.0.1:8765/subsidence/knowledge-base.html](http://127.0.0.1:8765/subsidence/knowledge-base.html)

接口约定见 [地降所项目AI应用开发接口文档.md](../../../docs/基于地降所项目改造/改造后前端UI及对应接口调用说明/地降所项目AI应用开发接口文档.md)。正式前端 UI 可参照 `http://localhost:3300`（admin/123456，验证码可空）。

鉴权：`Authorization: Bearer <SERVICE_API_KEY>`。各页默认 API 根 `http://127.0.0.1:8083`。

进程开关：

- 智能问答：`CHATBOT_DOMAIN=subsidence`
- 自动报告：`ANALYSIS_AGENT_ENABLED=true`，`NL2SQL_BUSINESS_DOMAIN=subsidence`
- 数据查询：`DATA_QUERY_AGENT_ENABLED=true`（关闭时本组接口 503）
- 知识库：`RAG_REQUIRE_NAMESPACE=true`（上传/摄入须带 namespace；侧栏 `exclude_nl2sql=true`）

联调注意：

- 智能问答 / 数据查询 / 自动报告：**禁止展示 SQL**（不渲染 `nl2sql_sql`）
- 数据查询：点树只走 browse，不要打 run-stream；不调 `/libraries`；解析路径详参用 `hud_by_entity`，不调 `GET /hud`
- 知识库：上传与摄入分离；不做分类启用/优先级（文档 §4.2）
- 自动报告页只覆盖五种 `subsidence_*` 模版；沉降分布图由后端离线 PNG，不接交互 GIS

锅炉通用智能体页仍用上级 [analysis-agent-stream.html](../analysis-agent-stream.html)。
