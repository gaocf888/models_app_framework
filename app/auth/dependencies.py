"""
业务接口鉴权：Service API Key。

调用方在请求头携带：`Authorization: Bearer <service-api-key>`
有效密钥由环境变量 `SERVICE_API_KEYS`（逗号分隔，支持轮换多钥）或单个 `SERVICE_API_KEY` 配置。
密钥须由运维在本机用 `app.auth.keygen.generate_service_api_key` 生成后写入环境变量（用法见该模块文档字符串；
部署说明见 `app/app-deploy/README.md` / `README-simple-deploy.md`；完整安全说明见 `docs/Service-API-Key-认证与安全说明.md`。无运行时 HTTP 签发接口。
"""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

security = HTTPBearer(auto_error=False)


def _configured_keys() -> frozenset[str]:
    raw = os.getenv("SERVICE_API_KEYS") or os.getenv("SERVICE_API_KEY") or ""
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


def require_service_api_key(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> None:
    """校验 Bearer token 是否为已配置的 Service API Key 之一（常量时间比对）。"""
    keys = _configured_keys()
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SERVICE_API_KEYS is not configured",
        )
    if creds is None or (creds.scheme or "").lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header (expected Bearer token)",
        )
    token = creds.credentials or ""
    for k in keys:
        if len(token) == len(k) and hmac.compare_digest(token, k):
            return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid API key",
    )
