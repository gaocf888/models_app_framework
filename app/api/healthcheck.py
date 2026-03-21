from fastapi import APIRouter

from app.core.logging import get_logger


router = APIRouter()
logger = get_logger(__name__)


@router.get("/", summary="健康检查")
async def health() -> dict:
    """
    健康检查接口，用于存活/就绪探针。
    """
    return {"status": "ok"}

