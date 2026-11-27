# 地降 NL2SQL RAG 资产（摄入源）
# 运维摄入：POST /rag/documents/upsert 或 /rag/jobs/ingest；要点见企业级简版 §4.4。
# 业务包总说明见上级「配置项说明.md」

| 文件 | 命名空间 |
|------|----------|
| `nl2sql_schema.md` | `nl2sql_schema` |
| `nl2sql_biz_knowledge.md` | `nl2sql_biz_knowledge` |
| `nl2sql_biz_knowledge_fcb_layer0_stations.md` | 并入 `nl2sql_biz_knowledge`（见 profile.rag.biz_source_files） |
| `qa_examples_seed.md` | `nl2sql_qa_examples` |

## 周期沉降口径（须与代码一致）

- **公式**：`Δ = total_settle(窗末) − total_settle(窗初)`（末−初）
- **符号**：Δ<0 沉降，Δ>0 回弹（负沉正弹）
- **排序**：「沉降最大/偏大」→ `ORDER BY … ASC`（越负越前）
- **禁止**：`settle_start − settle_end`（初−末）

改上述口径或本目录 md 后，必须 **re-ingest** 三命名空间，否则线上少样本仍可能生成旧公式。

## 重新摄入（避免 422）

`POST /rag/jobs/ingest` **每篇文档必填** `doc_name`、`doc_version`、`content`（缺任一字段 FastAPI 返回 **422**）。推荐：

```json
{
  "documents": [
    {
      "doc_name": "nl2sql_schema",
      "doc_version": "settle_end_minus_start_v1",
      "namespace": "nl2sql_schema",
      "source_type": "markdown",
      "content": "<粘贴 nl2sql_schema.md 全文>",
      "replace_if_exists": true
    },
    {
      "doc_name": "nl2sql_biz_knowledge",
      "doc_version": "settle_end_minus_start_v1",
      "namespace": "nl2sql_biz_knowledge",
      "source_type": "markdown",
      "content": "<粘贴 nl2sql_biz_knowledge.md + layer0 摘要全文>",
      "replace_if_exists": true
    },
    {
      "doc_name": "qa_examples_seed",
      "doc_version": "settle_end_minus_start_v1",
      "namespace": "nl2sql_qa_examples",
      "source_type": "markdown",
      "content": "<粘贴 qa_examples_seed.md 全文>",
      "replace_if_exists": true
    }
  ]
}
```

`doc_version` 建议在口径变更时递增（如 `settle_end_minus_start_v1`），便于区分旧向量。

## 摄入后抽测（符号方向）

| 问句类型 | 期望 SQL / 结果 |
|----------|-----------------|
| 单站周期沉降 | `period_settle_mm = 窗末 − 窗初`；沉降站 Δ 为负 |
| 「沉降最大」TopN | `ORDER BY period_settle_mm ASC`（最负在前） |
| 「哪些行政区近期沉降偏大」 | 区均 Δ 升序；骨架见 `district_period_agg` |
| 「回弹」 | 关注 Δ>0，勿写成 Δ<0 |

运行时对「哪些行政区…偏大」等区级问句，服务还会在召回结果前**注入**区均周期 CTE 金样（`district_period_agg_inject`），不替代正式摄入。
