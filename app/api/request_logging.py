"""
API 层 HTTP 请求中文日志。

在请求进入、正常返回与未捕获异常时输出结构化中文日志，便于在日志平台中检索与排障。
仅作用于 HTTP 边界，不侵入 service / LLM 等下层模块。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Final

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger

logger = get_logger("app.api.request")

# 高频探针/指标路径，默认不记录以免刷屏
_SKIP_EXACT_PATHS: Final[frozenset[str]] = frozenset(
    {
        "/metrics",
        "/health",
        "/health/",
        "/api/health",
    }
)

CallNext = Callable[[Request], Awaitable[Response]]


def _client_host(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "-"


def _request_id(request: Request) -> str | None:
    for header in ("x-request-id", "x-correlation-id"):
        value = (request.headers.get(header) or "").strip()
        if value:
            return value[:128]
    return None


def _query_summary(request: Request, *, max_pairs: int = 8, max_value_len: int = 80) -> str:
    if not request.query_params:
        return ""
    parts: list[str] = []
    for key, value in request.query_params.multi_items():
        if len(parts) >= max_pairs:
            parts.append("...")
            break
        v = value if len(value) <= max_value_len else value[: max_value_len - 3] + "..."
        parts.append(f"{key}={v}")
    return "&".join(parts)


def should_skip_request_logging(path: str, skip_path_prefixes: tuple[str, ...] = ()) -> bool:
    """判断是否跳过该路径的请求日志（探针、静态资源等）。"""
    if path in _SKIP_EXACT_PATHS:
        return True
    for prefix in skip_path_prefixes:
        if prefix and path.startswith(prefix):
            return True
    return False


def register_api_request_logging(
    app: FastAPI,
    *,
    skip_path_prefixes: tuple[str, ...] = (),
) -> None:
    """
    注册 API 请求日志中间件。

    应在业务路由挂载完成后调用；后注册的外层中间件会先收到请求、最后写回响应。
    """

    @app.middleware("http")
    async def api_request_logging_middleware(request: Request, call_next: CallNext) -> Response:
        path = request.url.path
        if should_skip_request_logging(path, skip_path_prefixes):
            return await call_next(request)

        method = request.method
        client = _client_host(request)
        rid = _request_id(request)
        query = _query_summary(request)
        start = time.perf_counter()

        rid_part = f"，请求ID={rid}" if rid else ""
        query_part = f"，查询参数={query}" if query else ""
        logger.info(
            "[API] 请求开始：%s %s，客户端=%s%s%s",
            method,
            path,
            client,
            rid_part,
            query_part,
        )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "[API] 请求异常：%s %s，耗时=%.1fms，异常类型=%s，说明=%s%s",
                method,
                path,
                duration_ms,
                type(exc).__name__,
                exc,
                rid_part,
            )
            raise

        duration_ms = (time.perf_counter() - start) * 1000
        status = response.status_code
        if status >= 500:
            level = logger.error
            outcome = "服务端错误"
        elif status >= 400:
            level = logger.warning
            outcome = "客户端错误"
        else:
            level = logger.info
            outcome = "成功"

        level(
            "[API] 请求完成（%s）：%s %s，状态码=%s，耗时=%.1fms%s",
            outcome,
            method,
            path,
            status,
            duration_ms,
            rid_part,
        )
        return response
