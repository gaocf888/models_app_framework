# GraphRAG 整体实现技术说明（大纲）

> 与 [`RAG整体实现技术说明.md`](RAG整体实现技术说明.md) 同级；描述 Neo4j 知识图谱基座、LLM 抽取与 `/graph/*` 运维面。  
> **默认全部关闭**：`GRAPH_RAG_ENABLED=false`、`GRAPH_RAG_INGEST_ON_RAG=false`、`GRAPH_RAG_MODE=vector`。

---

## 1. 架构定位

| 基座 | 部署 | 应用模块 |
|------|------|----------|
| 向量 RAG | `rag_db-deploy/` | `app/rag/*`、`/rag/*` |
| 图 GraphRAG | `graphrag_db-deploy/` | `app/graph/*`、`/graph/*` |
| 大模型 | `vllm-deploy/` | `VLLMHttpClient`、LangChain |

GraphRAG 为 **可选增强层**：向量摄入与检索主链路不变；图写入与混合检索需显式开启。

---

## 2. 配置开关（推荐启用顺序）

1. 部署 Neo4j：`graphrag_db-deploy`
2. 执行 init：`graphrag_db-deploy/init/01-constraints-indexes.cypher`
3. `GRAPH_RAG_ENABLED=true` + `NEO4J_*`
4. 按需：`GRAPH_RAG_INGEST_ON_RAG=true`（RAG 联动写图）
5. 按需：`GRAPH_RAG_DELETE_ON_RAG=true`（RAG 删文档同步删图）
6. 按需：`GRAPH_RAG_MODE=hybrid`（对话混合检索）

LLM 抽取：`GRAPH_EXTRACTION_MODE=llm`（默认）；`GRAPH_LLM_*` 空则复用默认 chat 模型。

---

## 3. 模块映射

| 能力 | 路径 |
|------|------|
| 配置 | `app/core/config.py` → `GraphRAGConfig` |
| Schema | `app/graph/schema_loader.py`、`configs/graph_schema.yaml.example` |
| LLM 抽取 | `app/graph/extraction/llm_extractor.py`、`configs/graph_extraction.yaml` |
| 写图 | `app/graph/ingestion.py` |
| 查图 | `app/graph/query_service.py` |
| 混合检索 | `app/rag/hybrid_rag_service.py` |
| RAG 联动 | `app/rag/ingestion.py`（`post_index_hook`，需 `ingest_on_rag`） |
| 运维 API | `app/api/graph_admin.py` → `/graph/*` |
| 运维服务 | `app/graph/admin_service.py` |

---

## 4. 数据模型（Neo4j）

- 节点：`DocumentChunk`、`Entity`
- 关系：`MENTION`（chunk→entity）、`GRAPH_REL`（entity→entity，属性 `rel_type`）、`CO_OCCUR`（规则/回退共现）

---

## 5. HTTP API（运维）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/graph/health` | 健康（disabled 时仍 200，enabled=false） |
| GET | `/graph/stats` | 统计（需 enabled） |
| GET | `/graph/schema` | 当前 Schema |
| POST | `/graph/schema/reload` | 热加载（需 HOT_RELOAD） |
| POST | `/graph/rebuild` | 重建（默认 `async_mode=true` 返回 job_id） |
| GET | `/graph/jobs/{job_id}` | 查询重建任务 |
| GET | `/graph/jobs` | 列出近期重建任务 |
| DELETE | `/graph/documents/{doc_name}` | 删图侧文档 |
| POST | `/graph/query/debug` | 图事实预览 |

---

## 6. 与 RAG 的关系

```
/rag/jobs/ingest → 向量库（必走）
                ↘ post_index_hook → GraphIngestionService（仅 ingest_on_rag=true）

/chatbot → HybridRAGService → RAGService（mode=vector 时与纯 RAG 一致）
```

---

## 7. 实施清单

详见 [`docs/知识图谱基座能力完善计划(临时).md`](../docs/知识图谱基座能力完善计划(临时).md)。
