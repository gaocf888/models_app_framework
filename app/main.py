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

    app = FastAPI(title="Models App Framework", version="0.1.0")

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
    from app.api import analysis, chatbot, llm_inference, nl2sql, rag_admin, small_model, train_admin

    app.include_router(llm_inference.router, prefix="/llm", tags=["llm"])
    app.include_router(chatbot.router, prefix="/chatbot", tags=["chatbot"])
    app.include_router(analysis.router, prefix="/analysis", tags=["analysis"])
    app.include_router(small_model.router, prefix="/small-model", tags=["small-model"])
    app.include_router(nl2sql.router, prefix="/nl2sql", tags=["nl2sql"])
    app.include_router(rag_admin.router, prefix="/rag", tags=["rag-admin"])
    app.include_router(train_admin.router, prefix="/dajia", tags=["dajia-admin"])

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

