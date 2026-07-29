# Analysis Trace 告警规则（权威副本）

本目录文件供文档与其它栈引用。

**运行中的 Prometheus 加载路径**：`monitoring-deploy/prometheus/rules/analysis-trace-alert-rules.yml`

修改本文件后，请同步复制到 `monitoring-deploy/prometheus/rules/`，并执行：

```bash
curl -X POST http://127.0.0.1:9090/-/reload
```
