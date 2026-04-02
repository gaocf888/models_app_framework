from fastapi import FastAPI
from prometheus_client import Counter, Summary, generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.core.config import get_app_config
from app.core.logging import setup_logging
from app.core.metrics import REQUEST_COUNT, REQUEST_LATENCY
from app.api import healthcheck


def create_app() -> FastAPI:
    """
    应用工厂：创建并配置 FastAPI 实例。
    """
    # 初始化配置与日志
    _ = get_app_config()
    setup_logging()

    tags_metadata = [
        {
            "name": "health",
            "description": (
                "存活/就绪探针与基础连通性检查，无业务依赖。"
                "返回 JSON 如 `{\"status\": \"ok\"}`，供负载均衡与编排使用。"
            ),
        },
        # {
        #     "name": "llm",
        #     "description": (
        #         "大模型推理 HTTP API（OpenAI 兼容调用形态）。"
        #         "需配置 `LLM_DEFAULT_ENDPOINT` / `LLM_DEFAULT_MODEL` 等；与 vLLM 等推理服务对接。"
        #     ),
        # },
        {
            "name": "chatbot",
            "description": (
                "智能客服对话：支持多轮会话（Redis）、可选 **RAG 混合检索**（语义+关键词+RRF+重排，见 `RAG_*` 配置）"
                "与可选多模态 `image_urls`。业务侧主要通过 `enable_rag` / `enable_context` 控制行为。"
            ),
        },
        # {
        #     "name": "analysis",
        #     "description": (
        #         "分析类对话/任务接口，可选挂载 RAG 上下文，参数与场景 profile 由 `RAG_SCENE_ANALYSIS_*` 等环境变量控制。"
        #     ),
        # },
        # {
        #     "name": "small-model",
        #     "description": (
        #         "小模型（如 YOLO）GPU 推理相关路由；依赖独立镜像与权重挂载，与 RAG 知识库无直接耦合。"
        #     ),
        # },
        # {
        #     "name": "nl2sql",
        #     "description": (
        #         "自然语言转 SQL 等能力；可选结合 RAG 增强时需配置向量库与嵌入模型（`RAG_ES_*`、`EMBEDDING_MODEL_*`）。"
        #     ),
        # },
        {
            "name": "rag-admin",
            "description": """
**RAG 管理（知识库运维面）**

与对话里的「自动 RAG」不同：本组接口用于**显式管理**知识文档生命周期与索引运维。

**推荐集成路径**
- **摄入（生产）**：`POST /rag/jobs/ingest` 异步任务 → `GET /rag/jobs/{job_id}` 轮询状态。
- **单篇同步修订**：`POST /rag/documents/upsert`。
- **删除**：`POST /rag/documents/delete`。
- **检索冒烟/调试**：`POST /rag/query`（直连 `RAGService` 混合检索；**未经过**对话侧的 `HybridRAGService` 图路由，与 `GRAPH_RAG_ENABLED=true` 时的聊天链路可能不一致）。

**数据与索引**
- 向量 + 全文写入 EasySearch/ES（`RAG_ES_*`）；文档元数据、任务状态见 `RAG_ES_DOCS_INDEX_*`、`RAG_ES_JOBS_INDEX_*`。
- **索引迁移**（低频）：`POST /rag/migrations/chunks/*` 需与嵌入维度一致。

**运营与清单**
- `GET /rag/documents/meta`、`/rag/documents/overview`、`/rag/knowledge/trends` 用于管理台统计。

> 大文档/PDF 摄入可能触发 MinerU、嵌入与 IO，请注意超时与并发（`MINERU_*`、`RAG_INGEST_*`）。
""",
        },
        {
            "name": "dajia-admin",
            "description": (
                "大模型训练/管理类管理接口（`/dajia`）；默认可能未挂载。"
                "生产环境建议通过网关限制访问。"
            ),
        },
    ]

    app = FastAPI(title="AI App Platform", version="1.0.0", openapi_tags=tags_metadata)

    # CORS 配置可根据实际需要调整/读取配置
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    app.include_router(healthcheck.router, prefix="/health", tags=["health"])

    @app.get(
        "/api/health",
        tags=["health"],
        summary="健康检查（/api 前缀兼容）",
    )
    async def health_api_prefix() -> dict:
        """
        与 `GET /health/` 等价。部分负载均衡、网关或外部探针固定访问 `/api/health`，
        避免 404 刷屏；应用主路由未使用全局 `/api` 前缀时仍需单独挂载此路径。
        """
        return {"status": "ok"}

    from app.api import analysis, chatbot, llm_inference, nl2sql, rag_admin, small_model, train_admin

    # app.include_router(llm_inference.router, prefix="/llm", tags=["llm"])  # 阶段暂时屏蔽，随开发计划开放
    app.include_router(chatbot.router, prefix="/chatbot", tags=["chatbot"])
    # app.include_router(analysis.router, prefix="/analysis", tags=["analysis"])  # 阶段暂时屏蔽，随开发计划开放
    # app.include_router(small_model.router, prefix="/small-model", tags=["small-model"])  # 阶段暂时屏蔽，随开发计划开放
    # app.include_router(nl2sql.router, prefix="/nl2sql", tags=["nl2sql"])  # 阶段暂时屏蔽，随开发计划开放
    app.include_router(rag_admin.router, prefix="/rag", tags=["rag-admin"])
    # app.include_router(train_admin.router, prefix="/dajia", tags=["dajia-admin"])

    # Prometheus /metrics 端点
    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        data = generate_latest()
        return PlainTextResponse(content=data, media_type=CONTENT_TYPE_LATEST)

    # 简单的全局中间件，用于统计请求次数与时延
    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        from time import perf_counter

        start = perf_counter()
        response = await call_next(request)
        duration = perf_counter() - start

        REQUEST_COUNT.labels(method=request.method, path=request.url.path, status=response.status_code).inc()
        REQUEST_LATENCY.labels(method=request.method, path=request.url.path).observe(duration)

        return response

    return app


app = create_app()

