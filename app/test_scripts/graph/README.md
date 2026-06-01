# GraphRAG F2 联调脚本

## 前置条件

- 应用已启动（默认 `http://127.0.0.1:8000`）
- Neo4j 已部署且应用 `.env` 中：
  - `GRAPH_RAG_ENABLED=true`
  - `NEO4J_*` 已配置
- 可选验证开关：
  - `GRAPH_RAG_INGEST_ON_RAG=true` — RAG 摄入联动写图
  - `GRAPH_RAG_DELETE_ON_RAG=true` — RAG 删文档同步删图
- LLM（vLLM）可用（`GRAPH_EXTRACTION_MODE=llm`）
- 环境变量 `SERVICE_API_KEY` 与业务 API 一致

## 脚本

- `graph_rag_f2_e2e.py`：F2 验收链路
  - `GET /graph/health`
  - RAG 异步摄入（可选）
  - `POST /graph/rebuild`（async_mode=true）→ 轮询 `GET /graph/jobs/{job_id}`
  - `GET /graph/stats`
  - `POST /graph/query/debug`
  - `POST /rag/documents/delete`

## 运行

```bash
set SERVICE_API_KEY=your-key
python app/test_scripts/graph/graph_rag_f2_e2e.py --base-url http://127.0.0.1:8000
```

若 Graph 未开启，脚本打印 `[SKIP]` 并以 0 退出（不阻断 CI 冒烟）。

仅测 rebuild/stats（跳过 RAG 摄入）：

```bash
python app/test_scripts/graph/graph_rag_f2_e2e.py --skip-rag-ingest
```
