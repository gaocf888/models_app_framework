from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

from app.core.config import get_app_config
from app.core.logging import setup_logging
from app.core.metrics import REQUEST_COUNT, REQUEST_LATENCY
from app.auth.dependencies import require_service_api_key
from app.api import healthcheck
from app.conversation.ids import ConversationIdValidationError


def create_app() -> FastAPI:
    """
    应用工厂：创建并配置 FastAPI 实例。
    """
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
        {
            "name": "chatbot",
            "description": (
                "智能客服：多轮会话（Redis）、可选 RAG、流式 SSE；"
                "会话管理：`GET /chatbot/sessions`、`GET/DELETE /chatbot/sessions/messages`、`PATCH /chatbot/sessions/title`。"
                "业务路由须在 Header 携带 `Authorization: Bearer <SERVICE_API_KEY>`；"
                "密钥由 `app.auth.keygen.generate_service_api_key` 生成后写入环境变量（见 `app/app-deploy/README.md`）。"
            ),
        },
        {
            "name": "rag-admin",
            "description": """
**RAG 管理（知识库运维面）**

与对话里的「自动 RAG」不同：本组接口用于**显式管理**知识文档生命周期与索引运维。

**鉴权**：须携带 `Authorization: Bearer <SERVICE_API_KEY>`（生成与配置同 `app/auth/keygen.py`、`app/app-deploy/README.md`「Service API Key」）。

**推荐集成路径**
- **摄入（生产）**：`POST /rag/jobs/ingest` 异步任务 → `GET /rag/jobs/{job_id}` 轮询状态。
- **单篇同步修订**：`POST /rag/documents/upsert`。
- **文档 namespace 迁移（向量 + docs 登记；GraphRAG 默认异步修复）**：`POST /rag/documents/namespace/move`（`repair_graph_async`）。
- **删除**：`POST /rag/documents/delete`。
- **检索冒烟/调试**：`POST /rag/query`。

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
                "大模型训练/管理类管理接口（`/dajia`）。"
                "须携带 `Authorization: Bearer <SERVICE_API_KEY>`（密钥生成见 `app/auth/keygen.py` 与部署文档）。"
            ),
        },
    ]

    app = FastAPI(title="AI App Platform", version="1.0.0", openapi_tags=tags_metadata)
    cfg = get_app_config()
    media_path = cfg.chatbot.image_public_path.strip() or "/chatbot/media"
    if not media_path.startswith("/"):
        media_path = "/" + media_path
    media_dir = Path(cfg.chatbot.image_store_dir.strip() or "runtime/chatbot_images")
    if not media_dir.is_absolute():
        media_dir = (Path(__file__).resolve().parent / media_dir).resolve()
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount(media_path.rstrip("/"), StaticFiles(directory=str(media_dir), check_dir=False), name="chatbot-media")

    @app.exception_handler(ConversationIdValidationError)
    async def _conversation_id_validation_handler(
        _request: Request, exc: ConversationIdValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(healthcheck.router, prefix="/health", tags=["health"])

    @app.get(
        "/api/health",
        tags=["health"],
        summary="健康检查（/api 前缀兼容）",
    )
    async def health_api_prefix() -> dict:
        return {"status": "ok"}

    from app.api import analysis, chatbot, llm_inference, nl2sql, rag_admin, small_model, train_admin

    _auth = [Depends(require_service_api_key)]

    app.include_router(
        llm_inference.router,
        prefix="/llm",
        tags=["llm"],
        dependencies=_auth,
    )
    app.include_router(
        chatbot.router,
        prefix="/chatbot",
        tags=["chatbot"],
        dependencies=_auth,
    )
    # 企业版综合分析 V2：双入口执行 + trace 运维（实现见 app/api/analysis.py）
    app.include_router(
        analysis.router,
        prefix="/analysis",
        tags=["analysis"],
        dependencies=_auth,
    )
    app.include_router(
        small_model.router,
        prefix="/small-model",
        tags=["small-model"],
        dependencies=_auth,
    )
    app.include_router(
        nl2sql.router,
        prefix="/nl2sql",
        tags=["nl2sql"],
        dependencies=_auth,
    )
    app.include_router(
        rag_admin.router,
        prefix="/rag",
        tags=["rag-admin"],
        dependencies=_auth,
    )
    app.include_router(
        train_admin.router,
        prefix="/dajia",
        tags=["dajia-admin"],
        dependencies=_auth,
    )

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        data = generate_latest()
        return PlainTextResponse(content=data, media_type=CONTENT_TYPE_LATEST)

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        from time import perf_counter

        start = perf_counter()
        response = await call_next(request)
        duration = perf_counter() - start

        REQUEST_COUNT.labels(method=request.method, path=request.url.path, status=response.status_code).inc()
        REQUEST_LATENCY.labels(method=request.method, path=request.url.path).observe(duration)

        return response

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema
        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            routes=app.routes,
            tags=app.openapi_tags,
        )
        openapi_schema.setdefault("components", {}).setdefault("securitySchemes", {})["ServiceApiKey"] = {
            "type": "http",
            "scheme": "bearer",
            "description": (
                "在环境变量中配置 SERVICE_API_KEYS（逗号分隔，可轮换）或 SERVICE_API_KEY；"
                "将其中一个密钥作为 Bearer token 发送。"
                "密钥由运维用 app.auth.keygen.generate_service_api_key 在本机生成（见 keygen 模块说明与 app/app-deploy/README.md）。"
            ),
        }
        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app


app = create_app()
