"""OpenAPI / Swagger / ReDoc HTTP Basic 保护。

默认关闭 FastAPI 内置无鉴权文档路由，改为自定义 `/docs`、`/redoc`、`/openapi.json`，
凭据来自环境变量（勿硬编码进镜像）。
"""

from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_security = HTTPBasic(auto_error=True)


def docs_basic_auth_enabled() -> bool:
    """默认开启；本地可设 DOCS_BASIC_AUTH_ENABLED=false。"""
    raw = (os.getenv("DOCS_BASIC_AUTH_ENABLED") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _docs_username() -> str:
    return (os.getenv("DOCS_USERNAME") or os.getenv("DOCS_USER") or "").strip()


def _docs_password() -> str:
    return (os.getenv("DOCS_PASSWORD") or os.getenv("DOCS_PASS") or "").strip()


def check_docs_credentials(
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
) -> str:
    """校验文档页 Basic 账号；失败返回 401 + WWW-Authenticate。"""
    expected_user = _docs_username()
    expected_pass = _docs_password()
    if not expected_user or not expected_pass:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API docs credentials are not configured",
            headers={"WWW-Authenticate": "Basic"},
        )
    user_ok = secrets.compare_digest(credentials.username, expected_user)
    pass_ok = secrets.compare_digest(credentials.password, expected_pass)
    if not (user_ok and pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


def register_protected_docs(app: FastAPI) -> None:
    """注册受保护的 /docs、/redoc、/openapi.json（须在 docs_url=None 等关闭默认路由后调用）。"""

    if docs_basic_auth_enabled():
        deps = [Depends(check_docs_credentials)]
    else:
        deps = []

    @app.get("/openapi.json", include_in_schema=False, dependencies=deps)
    async def openapi_json() -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False, dependencies=deps)
    async def swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - Docs",
        )

    @app.get("/redoc", include_in_schema=False, dependencies=deps)
    async def redoc_ui() -> HTMLResponse:
        return get_redoc_html(
            openapi_url="/openapi.json",
            title=f"{app.title} - ReDoc",
        )
