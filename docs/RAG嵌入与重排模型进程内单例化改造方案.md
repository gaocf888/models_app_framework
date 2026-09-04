# RAG 嵌入与重排模型进程内单例化改造方案

> **目标**：消除 `models-app` 启动与运行期对 **同一嵌入/重排权重** 的重复加载，降低 GPU/RAM 占用与 OOM 概率，**不改变** 各基座与业务模块对外行为与 API 契约。  
> **适用范围**：`app/rag/`、`app/nl2sql/`、依赖 RAG 的 `services/` / `api/` / `llm/graphs/` / `llm/chains/`。  
> **关联现状**：单卡 3090 与 vLLM 同机部署时多次出现 `EmbeddingService` CUDA OOM；根因分析表明 **vLLM 占显存 + 进程内 7 份 embed 重复加载** 叠加导致。

> **关联**：昇腾生产默认亦可走独立 **MIS-TEI**（`EMBEDDING_BACKEND=mis_tei`），此时进程内不加载 ST 权重，单例化问题主要适用于 **`local`** 后端。部署见 `mis-tei-deploy/README.md`。

---

## 1. 背景与问题

### 1.1 现象

- `models-app` 启动失败：`EmbeddingService: CUDA out of memory`，栈常落在 `chatbot` / `nl2sql` 链创建第 5～6 个 `EmbeddingService` 时。
- 调低 vLLM `gpu_memory_utilization` 后仍可能在加载 embed 最后阶段 OOM（差数十 MiB）。
- 日志中 `models-app` 进程在失败前已达 **~6.66 GiB**，与「多份 Qwen3-Embedding-0.6B（FP16）已驻留 GPU」一致。

### 1.2 根因（代码层面）

1. **`EmbeddingService` / `RAGService` 无进程级单例**  
   `RAGService.__init__` 在未注入时执行 `EmbeddingService()`，每次新建完整模型。

2. **API 模块 import 时 eager 构造业务 Service**  
   `main.create_app()` 一次性 import 多个 `app.api.*`，各模块 `service = XxxService()` 在 **import 阶段** 触发 RAG/NL2SQL 链初始化。

3. **NL2SQL 与通用 RAG 未共享底层 `RAGService`**  
   `NL2SQLRAGService` 默认 `RAGService()`，与 `ChatbotService._rag` 等 **相互独立**，但使用的是 **同一 embed 权重、同一 ES 索引**，无隔离必要。

4. **重排模型懒加载但按 `RAGService` 实例隔离**  
   `_get_reranker()` 在每个 `RAGService` 上各加载一份 `CrossEncoder`，运行期可能叠 **最多 7 份 reranker**。

5. **`/llm/infer` 每请求 `new LLMInferenceService()`**  
   启动无影响；**运行期** 每次请求可能再加载 embed（若未命中单例）。

### 1.3 与 vLLM 同机时的显存账本（单 worker，FP16 embed）

| 组件 | 当前（约） | 单例化后（约） |
|------|------------|----------------|
| vLLM 14B/8B AWQ | 16～21 GiB | 不变 |
| Embedding × N | **N × 1.2～1.5 GiB**（启动 N≈7） | **1 × 1.2～1.5 GiB** |
| Reranker × M | **M × 1.2～1.5 GiB**（M≤RAG 实例数，懒加载） | **1 × 1.2～1.5 GiB** |
| **models-app 合计（embed+rerank）** | **8～21 GiB 级** | **2～3 GiB 级** |

> 说明：单例化 **不能替代**「与 vLLM 分卡 / embed·rerank 走 CPU」的运维策略，但可显著扩大「同卡 GPU RAG」的可行窗口。

---

## 2. 改造目标与非目标

### 2.1 目标

| 编号 | 目标 | 验收 |
|------|------|------|
| G1 | 单 worker 进程内 **至多 1 份** `EmbeddingService`、**1 份** `RAGService`（含 1 份 lazy reranker） | 启动日志仅 **1 条** `EmbeddingService: loaded ...`；`nvidia-smi` embed 驻留 ~1.5 GiB 而非 ~6+ GiB |
| G2 | **保持** 构造函数注入能力（测试与定制部署） | 现有 `tests/` 传入 `_FakeEmbeddingService` 等 **零修改通过** |
| G3 | **不改变** HTTP API、响应结构、RAG/NL2SQL/Chatbot/Analysis 业务语义 | 回归用例与手工冒烟与改造前一致 |
| G4 | **不改变** `.env` 配置项含义（`EMBEDDING_*`、`RAG_RERANKER_*` 仍生效） | 无需强制改部署 env |
| G5 | 与现有 `rag_admin` 的 `@lru_cache` 模式 **对齐**，形成统一注册表 | `RAGIngestionService` 与业务链共用同一 embed |

### 2.2 非目标（本方案不做）

- **不** 改造 vLLM 部署参数（见 `vllm-deploy/config/models.yaml` 独立运维）。
- **不** 在本期实现 GPU OOM → CPU 兜底（可列为 Phase 2 增强，与单例化正交）。
- **不** 合并 `NL2SQLService` / `ChatbotService` / `AnalysisService` 等业务门面（仅共享 RAG 基座）。
- **不** 强制 `UVICORN_WORKERS=1`（多 worker 下每 worker 1 份单例即可，见 §6.3）。

---

## 3. 现状全量清单

### 3.1 启动 import 链（`app/main.py`）

```text
create_app()
  └─ from app.api import analysis, analysis_agent, chatbot, ..., nl2sql, rag_admin, ...
       ├─ analysis.service = AnalysisService()
       │    ├─ RAGService()                    → Embed #1
       │    └─ NL2SQLService() → NL2SQLRAGService() → RAGService() → Embed #2
       ├─ analysis_agent.service = AnalysisAgentService()
       │    └─ SlotOrchestrator()
       │         ├─ HybridRAGService() → RAGService() → Embed #3
       │         └─ NL2SQLService() → … → Embed #4
       ├─ chatbot.service = ChatbotService()
       │    ├─ RAGService()                      → Embed #5
       │    └─ NL2SQLService() → … → Embed #6   ← 常见 OOM 栈
       ├─ nl2sql.service = NL2SQLService() → … → Embed #7
       ├─ rag_admin：@lru_cache 懒加载（启动不加载）
       └─ llm_inference：Depends 懒加载（启动不加载）
```

### 3.2 代码中「默认 new RAG / Embed」位置

| 文件 | 默认构造 | 启动是否触发 embed |
|------|----------|-------------------|
| `app/rag/rag_service.py` | `EmbeddingService()` | 是（经 RAGService） |
| `app/rag/hybrid_rag_service.py` | `RAGService()` | 是 |
| `app/nl2sql/rag_service.py` | `RAGService()` | 是 |
| `app/rag/ingestion.py` | `EmbeddingService()` + 注入 `RAGService` | 仅 rag_admin 首次调用 |
| `app/rag/agentic.py` | `RAGService()` | 随 LLM infer / ChatbotChain |
| `app/services/chatbot_service.py` | `RAGService()` + `NL2SQLService()` | 是 |
| `app/services/analysis_service.py` | `RAGService()` + `NL2SQLService()` | 是 |
| `app/analysis_agent/graph/orchestrator.py` | `HybridRAGService()` + `NL2SQLService()` | 是 |
| `app/llm/graphs/analysis_graph_runner.py` | `HybridRAGService()` + `NL2SQLService()` | 若未注入则触发 |
| `app/llm/chains/chatbot_chain.py` | `RAGService()` | Chatbot 启用 LangChain 时 |
| `app/services/llm_inference_service.py` | `RAGService()` | **每请求** |

### 3.3 已做对的模式（应对齐）

```python
# app/rag/ingestion.py — 同一服务内 embed 只建一次并注入 RAGService
self._embedding_service = embedding_service or EmbeddingService()
self._rag_service = RAGService(embedding_service=self._embedding_service, ...)

# app/api/rag_admin.py — 进程内懒加载单例
@lru_cache(maxsize=1)
def _get_service() -> RAGIngestionService: ...
```

### 3.4 为何 NL2SQL 与通用 RAG 可以共享同一 `RAGService`

- `NL2SQLRAGService` 仅为 **命名空间常量**（`nl2sql_schema` / `nl2sql_biz_knowledge` / `nl2sql_qa_examples`）与检索策略封装。
- 底层 `index_texts` / `retrieve_chunks` 均调用同一 `RAGService`，**无独立模型或索引**。
- 共享后：检索、摄入、维度、ES 字段 **行为不变**。

---

## 4. 目标架构

### 4.1 进程内依赖关系（改造后）

```text
                    ┌─────────────────────────┐
                    │  app.rag.service_registry │
                    │  (thread-safe lazy init)  │
                    └───────────┬─────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   get_embedding_service()  get_rag_service()   get_vector_store_provider()
          │                     │
          │                     ├── HybridRAGService(rag_service=shared)
          │                     ├── NL2SQLRAGService(rag_service=shared)
          │                     ├── RAGIngestionService(embed+rag shared)
          │                     └── AgenticRAGService(rag_service=shared)
          │
          └─ 全进程唯一 SentenceTransformer 权重
             全进程唯一 CrossEncoder（lazy，RAGService._get_reranker）
```

### 4.2 业务模块与基座关系（不变）

```text
/api/chatbot          → ChatbotService        ─┐
/api/analysis         → AnalysisService       ─┤ 各自保留独立
/api/analysis-agent   → AnalysisAgentService  ─┤ ConversationManager、
/api/nl2sql           → NL2SQLService         ─┤ LLM Client、GraphRunner
/api/rag-admin        → RAGIngestionService   ─┤
/api/llm/infer        → LLMInferenceService   ─┘
                              │
                              └─► 共享 get_rag_service() / get_embedding_service()
```

**原则**：只收敛 **有 GPU/RAM 成本的模型权重**；业务 Service 仍可多实例（无权重重复）。

---

## 5. 详细设计

### 5.1 新增模块：`app/rag/service_registry.py`

#### 5.1.1 公开 API（建议）

```python
def get_embedding_service() -> EmbeddingService: ...
def get_rag_service() -> RAGService: ...
def get_vector_store_provider() -> VectorStoreProvider: ...
def get_nl2sql_rag_service() -> NL2SQLRAGService: ...
def get_hybrid_rag_service() -> HybridRAGService: ...

def clear_rag_service_registry() -> None:
    """仅测试/fixture 使用：清空注册表，避免用例间泄漏。"""
```

#### 5.1.2 实现要点

| 要点 | 说明 |
|------|------|
| 懒加载 | 首次调用 `get_*` 时初始化，避免 import 环 |
| 双检锁 | `threading.Lock` + `if _x is None` 双重检查，兼容 uvicorn 启动与并发首次请求 |
| 延迟 import | `service_registry` 内 **延迟** `from app.rag.embedding_service import EmbeddingService`，避免与 `rag_service` 循环依赖 |
| 日志 | 单例创建时 INFO：`RAG registry: created singleton EmbeddingService instance_id=...`；**禁止** 7 次 `EmbeddingService: loaded` |
| 实例 ID | 可选 `id(self)` 或 uuid 便于日志核对「仅一份」 |

#### 5.1.3 `get_rag_service()` 构造

```python
RAGService(
    embedding_service=get_embedding_service(),
    store_provider=get_vector_store_provider(),
)
```

- `VectorStoreProvider` 单例化：**不增加 GPU 占用**，但减少 ES 连接重复（建议一并纳入 registry）。

#### 5.1.4 `get_nl2sql_rag_service()` / `get_hybrid_rag_service()`

- 包装 **同一** `get_rag_service()` 返回值。
- `HybridRAGService` 可每次 `new`（轻量，仅 Graph 客户端），或 registry 再包一层 `@lru_cache`；**关键**是传入 `rag_service=get_rag_service()`。

### 5.2 修改「默认工厂」策略（核心）

**规则**：凡 `xxx or EmbeddingService()` / `xxx or RAGService()` 的 **默认分支**，改为 registry getter；**显式传入** 时行为与现在完全一致。

| 文件 | 改前 | 改后 |
|------|------|------|
| `rag_service.py` | `embedding_service or EmbeddingService()` | `embedding_service or get_embedding_service()` |
| `rag_service.py` | `store_provider or VectorStoreProvider()` | `store_provider or get_vector_store_provider()` |
| `hybrid_rag_service.py` | `rag_service or RAGService()` | `rag_service or get_rag_service()` |
| `nl2sql/rag_service.py` | `rag_service or RAGService()` | `rag_service or get_rag_service()` |
| `rag/agentic.py` | `rag_service or RAGService()` | `rag_service or get_rag_service()` |
| `rag/ingestion.py` | 独立 `EmbeddingService()` | `get_embedding_service()` + `get_rag_service()` 或注入同一 embed |
| `services/llm_inference_service.py` | `rag_service or RAGService()` | `rag_service or get_rag_service()` |
| `llm/chains/chatbot_chain.py` | `rag_service or RAGService()` | `rag_service or get_rag_service()` |

> **注意**：`RAGService()` 若仍被测试直接调用且 **不传** embed，也会走 `get_embedding_service()`，自然共享单例——符合预期。

### 5.3 API 层改造（可选增强，建议 Phase 1 末）

模块级 `service = XxxService()` **可保留**（减少 diff）；依赖单例后，多次 `NL2SQLService()` 不再重复加载 embed。

**推荐补充**（降低 NL2SQL 链重复初始化开销，非必须）：

```python
# app/api/nl2sql.py — 示例：与其他模块共享 NL2SQLService（无 GPU 模型，可选）
from app.services.service_registry_facade import get_nl2sql_service  # 薄封装
service = get_nl2sql_service()
```

`get_nl2sql_service()` 用 `@lru_cache` 返回同一 `NL2SQLService` 即可；**与 embed 单例独立**，属 P2 优化。

### 5.4 `/llm/infer` 改造

```python
# 改前
def get_service() -> LLMInferenceService:
    return LLMInferenceService()

# 改后
@lru_cache(maxsize=1)
def get_service() -> LLMInferenceService:
    return LLMInferenceService()
```

或 `LLMInferenceService(rag_service=get_rag_service())` + `@lru_cache`。  
**保证**：多次 infer **不重复加载** embed/rerank。

### 5.5 `rag_admin` 与 registry 统一

```python
@lru_cache(maxsize=1)
def _get_service() -> RAGIngestionService:
    return RAGIngestionService(
        embedding_service=get_embedding_service(),
        store_provider=get_vector_store_provider(),
    )
```

- 删除 `RAGIngestionService` 内部二次 `RAGService(embedding_service=...)` 的 **隐式第二路径**（已注入同一 embed，保持现状逻辑即可）。

### 5.6 循环依赖规避

```text
service_registry.py  ──延迟 import──► embedding_service.py
                    ──延迟 import──► rag_service.py
rag_service.py      ──import──► service_registry.get_embedding_service
embedding_service.py  不 import rag_service / registry
```

单元测试 `clear_rag_service_registry()` 在 `pytest` fixture `autouse` 或各 RAG 测试 `teardown` 调用，**避免** 测试顺序导致「假单例」污染。

---

## 6. 兼容性与行为保证

### 6.1 对业务模块「零语义变更」论证

| 维度 | 改造前 | 改造后 | 是否等价 |
|------|--------|--------|----------|
| 嵌入向量维度 | 各实例相同 config | 同一模型 | ✅ |
| ES 检索 / 写入 | 同一 index、同一 vector_field | 同一 `VectorStoreProvider` | ✅ |
| NL2SQL namespace 检索 | `NL2SQLRAGService` 逻辑 | 包装同一 `RAGService`，逻辑不变 | ✅ |
| Hybrid / GraphRAG | `HybridRAGService` 策略 | 同一底层 rag | ✅ |
| CrossEncoder 重排 | 每 RAGService 一份 | 一份；分数应一致 | ✅ |
| 并发请求 | 多实例只读 inference | 单实例只读 inference | ✅ 见 §6.2 |
| 单元测试注入 Fake | `RAGService(embedding_service=fake)` | 仍绕过 registry | ✅ |

### 6.2 线程与并发

| 场景 | 风险 | 对策 |
|------|------|------|
| `UVICORN_WORKERS=1`（默认） | 单进程多协程并发 `encode` | ST/PyTorch eval 模式通常只读；若压测出现竞态，在 `EmbeddingService.encode*` 外加 `threading.Lock`（**按需**，Phase 2） |
| `UVICORN_WORKERS>1` | 每 worker 各 1 份单例 | 可接受：2 worker ≈ 2×embed，仍远优于 2×7 |
| Reranker lazy init | 双请求同时 `_get_reranker` | 已有 `_reranker_lock`，保持 |

### 6.3 多 worker 与部署

- `app/app-deploy` 默认 `UVICORN_WORKERS:-1`：**改造收益最大**。
- 若 `UVICORN_WORKERS=4`：GPU embed 约 **4×1.5 GiB**；运维文档需说明「worker 数 × embed 显存」。
- **不建议** 为单卡 3090 提高 worker 数同时 GPU embed。

### 6.4 配置与环境变量

- **不新增** 必填 env。
- 可选开关（仅调试）：`RAG_SERVICE_REGISTRY_DISABLE=false` — 设为 true 时回退 `EmbeddingService()` 直接 new（**仅开发/对比**，默认 false）。  
  生产可不实现该开关，以减少分支。

---

## 7. 分阶段实施计划

### Phase 0：基线与观测（0.5 人日）

- [ ] 在测试环境记录改造前：`grep -c "EmbeddingService: loaded"` 启动日志次数、`nvidia-smi` 驻留。
- [ ] 固定用例：`/chatbot/chat/stream`、`/analysis/run-with-nl2sql`、`/nl2sql/query`、`/rag/query` 冒烟通过截图。

### Phase 1：Registry + 默认工厂替换（1～1.5 人日）— **必做**

- [ ] 新增 `app/rag/service_registry.py` + 单元测试 `test_rag_service_registry.py`。
- [ ] 修改 §5.2 所列文件的默认分支为 `get_*()`。
- [ ] `llm_inference.get_service` 改为 `@lru_cache`。
- [ ] `rag_admin._get_service` 对齐 registry。

**验收**：启动仅 1 条 embed loaded；原 `tests/test_rag_*.py`、`tests/test_nl2sql_*.py` 全绿。

### Phase 2：API 层 NL2SQL 服务复用（0.5 人日）— 可选

- [ ] `get_nl2sql_service()` 单例，供 `analysis` / `chatbot` / `nl2sql` / `analysis_agent` 模块级 `service` 引用。
- [ ] 减少 `NL2SQLChain` / `SQLExecutor` 重复初始化（CPU/连接开销，非 GPU）。

### Phase 3：文档与运维（0.5 人日）

- [ ] 更新 `app/app-deploy/README.md`、`docker-nvidia/README.md`：启动日志期望、worker 与显存关系。
- [ ] 在 `大小模型应用技术架构与实现方案.md` 增加「RAG 模型单例 registry」小节（1 段引用本文档）。

### Phase 4：增强（与本方案解耦，按需）

- [ ] GPU OOM → CPU 兜底（embed/rerank 加载阶段）。
- [ ] `EmbeddingService.encode` 并发锁（压测后决定）。
- [ ] Prometheus 指标：`rag_embedding_singleton_created_total`、`rag_reranker_load_total`。

---

## 8. 测试策略

### 8.1 单元测试

| 用例 | 断言 |
|------|------|
| `test_registry_returns_same_embedding_instance` | `get_embedding_service() is get_embedding_service()` |
| `test_registry_returns_same_rag_instance` | `get_rag_service() is get_rag_service()` |
| `test_rag_service_injection_bypasses_registry` | 传入 `_FakeEmbeddingService` 时不调用 registry |
| `test_clear_registry_for_isolation` | `clear_*` 后新实例 id 不同 |
| 现有 `test_rag_namespace_kb` 等 | **不修改** Fake 注入，全量回归 |

### 8.2 集成 / 启动测试

```bash
# 启动后应恰好 1 次（或 0 次若懒加载改为首次请求，不推荐懒加载 embed）
docker compose ... logs models-app 2>&1 | grep -c "EmbeddingService: loaded"

# 显存（GPU embed 时）
nvidia-smi --query-compute-apps=pid,used_memory --format=csv
```

### 8.3 业务回归清单

| 模块 | 接口/路径 | 关注点 |
|------|-----------|--------|
| 智能客服 | `POST /chatbot/chat/stream` | RAG 引用、citation、rerank_score |
| 综合分析 | `POST /analysis/run-with-nl2sql` | NL2SQL 臂 + 业务 RAG |
| 分析智能体 | `POST /analysis-agent/run-stream` | 槽位 RAG enrichment |
| NL2SQL | `POST /nl2sql/query` | schema/biz/qa namespace 召回 |
| RAG 运维 | `POST /rag/jobs/ingest`、`POST /rag/query` | 摄入维度与检索一致 |
| LLM 推理 | `POST /llm/infer` + `enable_rag=true` | 连续 10 次请求显存不涨 |

---

## 9. 风险、回滚与发布

### 9.1 风险

| 风险 | 等级 | 缓解 |
|------|------|------|
| 循环 import | 中 | registry 内延迟 import；CI import smoke |
| 测试间单例泄漏 | 中 | `clear_rag_service_registry()` fixture |
| 并发 encode 竞态 | 低 | 默认单 worker；压测后再加锁 |
| 误以为单例可任意提高 worker | 中 | 文档明确 worker×embed |

### 9.2 回滚

- Phase 1 改动集中于 registry + 默认工厂 **一行替换**，回滚 revert 对应 commit 即可。
- 无数据库迁移、无 ES 索引变更。

### 9.3 发布建议

1. 先在 **与 vLLM 同机** 的 3090 环境验证启动与显存。  
2. 再发 **仅 CPU 栈** / **双卡 GPU** 环境（行为应一致，仅资源占用下降）。  
3. 观察 24h：`EmbeddingService` / `CrossEncoder reranker` ERROR/OOM 日志。

---

## 10. 改造前后对比（摘要）

```text
改造前（单 worker 启动）:
  EmbeddingService 实例数: 7
  RAGService 实例数:     7
  CrossEncoder 潜在实例:  7（首次 rerank 后）
  GPU embed 驻留（FP16）: ~8–10 GiB

改造后（单 worker 启动）:
  EmbeddingService 实例数: 1
  RAGService 实例数:     1
  CrossEncoder 实例:      1
  GPU embed 驻留（FP16）: ~1.2–1.5 GiB（+ rerank 懒加载 ~1.2–1.5 GiB）
```

---

## 11. 附录：文件改动清单（Phase 1）

| 操作 | 路径 |
|------|------|
| **新增** | `app/rag/service_registry.py` |
| **新增** | `tests/test_rag_service_registry.py` |
| **修改** | `app/rag/rag_service.py` |
| **修改** | `app/rag/hybrid_rag_service.py` |
| **修改** | `app/rag/ingestion.py` |
| **修改** | `app/rag/agentic.py` |
| **修改** | `app/nl2sql/rag_service.py` |
| **修改** | `app/services/llm_inference_service.py` |
| **修改** | `app/llm/chains/chatbot_chain.py` |
| **修改** | `app/api/llm_inference.py`（`@lru_cache`） |
| **修改** | `app/api/rag_admin.py`（`_get_service` 对齐 registry） |
| **文档** | `app/app-deploy/README.md`（启动日志说明） |

**无需修改**（仅通过默认工厂间接受益）：  
`chatbot_service.py`、`analysis_service.py`、`orchestrator.py`、`analysis_graph_runner.py`、`nl2sql/chain.py` 等——它们继续 `RAGService()` / `NL2SQLRAGService()` 即可自动共享单例。

---

## 12. 结论

- **确认存在** 嵌入模型 **7 重复加载**、重排模型 **最多 7 重复加载** 的问题，且与 **启动显存 OOM 强相关**。  
- 推荐通过 **`app/rag/service_registry.py` 进程内单例 + 默认工厂替换** 解决，**不改变** 各业务模块 API 与 RAG/NL2SQL 语义；测试可通过 **继续注入 Fake** 保持隔离。  
- 单例化后，同卡部署应优先选用 **Qwen3-8B-AWQ** 或调优后的 14B AWQ，并配合 **FP16 embed**；若仍紧张，运维侧 **`EMBEDDING_DEVICE=cpu`** 与单例化 **可叠加** 使用。

---

*文档版本：v1.0 | 与代码库分析日期：2026-07-08*
