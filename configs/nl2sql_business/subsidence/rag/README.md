# 地降 NL2SQL RAG 资产（摄入源）
# 运维摄入：POST /rag/documents/upsert 或 /rag/jobs/ingest；要点见企业级简版 §4.4。
# 业务包总说明见上级「配置项说明.md」

| 文件 | 命名空间 |
|------|----------|
| `nl2sql_schema.md` | `nl2sql_schema` |
| `nl2sql_biz_knowledge.md` | `nl2sql_biz_knowledge` |
| `nl2sql_biz_knowledge_fcb_layer0_stations.md` | 并入 `nl2sql_biz_knowledge`（见 profile.rag.biz_source_files） |
| `qa_examples_seed.md` | `nl2sql_qa_examples` |

## 重新摄入（避免 422）

`POST /rag/jobs/ingest` **每篇文档必填** `doc_name`、`doc_version`、`content`（缺任一字段 FastAPI 返回 **422**）。推荐：

```json
{
  "documents": [
    {
      "doc_name": "qa_examples_seed",
      "doc_version": "v1",
      "namespace": "nl2sql_qa_examples",
      "source_type": "markdown",
      "content": "<粘贴 qa_examples_seed.md 全文或服务端可读路径>",
      "replace_if_exists": true
    },
    {
      "doc_name": "nl2sql_biz_knowledge",
      "doc_version": "v1",
      "namespace": "nl2sql_biz_knowledge",
      "source_type": "markdown",
      "content": "<粘贴 nl2sql_biz_knowledge.md 全文或路径>",
      "replace_if_exists": true
    }
  ]
}
```

改口径后请同步 re-ingest 上述两个 namespace。  
运行时对「哪些行政区…偏大」等区级问句，服务还会在召回结果前**注入**区均周期 CTE 金样（`district_period_agg_inject`），不替代正式摄入。
