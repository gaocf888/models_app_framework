# RAG API Test Scripts

该目录用于管理 RAG 的端到端 API 集成测试脚本。

## 前置条件

- 应用已启动（默认 `http://127.0.0.1:8000`）
- RAG 管理路由已注册（`/rag/*`）
- 若使用 ES/EasySearch，请确保索引与连接配置已正确

## 脚本

- `rag_api_e2e.py`：覆盖以下链路
  - 已分块链路：ingest（首次写入）→ query → update（同名文档重灌）→ delete
  - 原始文档链路：raw_ingest（自动清洗与切块）→ query → raw_update → raw_delete
  - 异步任务链路：jobs/ingest（提交任务）→ jobs/{job_id}（状态轮询）→ query → delete
  - 可选迁移链路：chunks migration run / rollback（通过参数启用）
- `rag_migration_consistency_e2e.py`：migration 前后检索一致性回归
  - 基线摄入与查询
  - 迁移 run 后一致性比对（overlap ratio）
  - 可选 rollback 后一致性比对并恢复
  - 基线样本支持配置文件驱动（默认 `migration_consistency_cases.json`）
  - query 支持按场景配置（`llm_inference/chatbot/analysis/nl2sql`）
  - 支持输出 JSON 报告（用于 CI 门禁）
  - 支持输出 Markdown 汇总报告（便于流水线页面直读）
  - 失败时仍落盘 JSON/Markdown（含 `status`、`failed_phase`、`error`、`traceback`），便于 CI 诊断
- `rag_doc_lifecycle_e2e.py`：文档生命周期一致性回归
  - 同文档名多版本异步 upsert（`v1` -> `v2`）
  - 检索校验：`v2` 生效且 `v1` 旧内容不可检索
  - 元数据校验：文档元数据中存在 `v2 SUCCESS` 记录
  - 删除校验：`/documents/delete` 后查询为空（向量/图侧同步清理链路）
  - 支持输出 JSON/Markdown 报告（CI 门禁友好），失败时同样落盘诊断信息

## 运行示例

```bash
python app/test_scripts/rag/rag_api_e2e.py --base-url http://127.0.0.1:8000
```

如需验证迁移接口：

```bash
python app/test_scripts/rag/rag_api_e2e.py --base-url http://127.0.0.1:8000 --test-migration --migration-dim 512
```

migration 一致性回归：

```bash
python app/test_scripts/rag/rag_migration_consistency_e2e.py --base-url http://127.0.0.1:8000 --migration-dim 512 --consistency-threshold 0.6 --top-k 4
```

指定自定义样本文件：

```bash
python app/test_scripts/rag/rag_migration_consistency_e2e.py --base-url http://127.0.0.1:8000 --cases-file app/test_scripts/rag/migration_consistency_cases.json
```

输出结构化报告：

```bash
python app/test_scripts/rag/rag_migration_consistency_e2e.py --base-url http://127.0.0.1:8000 --report-out app/test_scripts/rag/output/migration_report.json
```

同时输出 Markdown 汇总：

```bash
python app/test_scripts/rag/rag_migration_consistency_e2e.py --base-url http://127.0.0.1:8000 --report-out app/test_scripts/rag/output/migration_report.json --report-md-out app/test_scripts/rag/output/migration_report.md
```

文档生命周期一致性回归：

```bash
python app/test_scripts/rag/rag_doc_lifecycle_e2e.py --base-url http://127.0.0.1:8000
```

输出 JSON/Markdown 报告：

```bash
python app/test_scripts/rag/rag_doc_lifecycle_e2e.py --base-url http://127.0.0.1:8000 --report-out app/test_scripts/rag/output/doc_lifecycle_report.json --report-md-out app/test_scripts/rag/output/doc_lifecycle_report.md
```

