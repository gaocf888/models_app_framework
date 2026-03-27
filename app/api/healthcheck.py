"""
健康检查接口。

服务配置前置条件（运维/开发）：
- 无强依赖参数；用于容器存活探针（liveness/readiness）和基础连通性检查。
"""

from fastapi import APIRouter

from app.core.logging import get_logger


router = APIRouter()
logger = get_logger(__name__)


@router.get("/", summary="健康检查")
async def health() -> dict:
    """
    健康检查接口，用于存活/就绪探针。

    参数说明：
    - 无必传参数
    """
    return {"status": "ok"}

