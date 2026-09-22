# tests/web/subsidence

地降所侧栏能力的浏览器联调页。勿用 `file://`，在 `tests/web` 起静态服务后访问：

```bash
cd tests/web
python -m http.server 8765
```

| 页面 | 用途 |
|------|------|
| [auto-report.html](auto-report.html) | **自动报告生成**：`POST /analysis-agent/run-stream` + `POST /analysis-agent/stream/stop` |

打开示例：[http://127.0.0.1:8765/subsidence/auto-report.html](http://127.0.0.1:8765/subsidence/auto-report.html)

进程：`ANALYSIS_AGENT_ENABLED=true`，`NL2SQL_BUSINESS_DOMAIN=subsidence`。鉴权 `Authorization: Bearer <SERVICE_API_KEY>`。

本页只覆盖五种 `subsidence_*` 模版；不展示 SQL、不接 GIS、不做 HITL resume。锅炉通用智能体页仍用上级 [analysis-agent-stream.html](../analysis-agent-stream.html)。
