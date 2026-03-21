from __future__ import annotations

from fastapi import APIRouter

from app.models.small_model import SmallModelChannelConfig, SmallModelChannelStatus
from app.services.small_model_channel_service import SmallModelChannelService

router = APIRouter()
service = SmallModelChannelService()


@router.post("/channel/start", summary="启动小模型通道")
async def start_channel(cfg: SmallModelChannelConfig) -> dict:
    service.start(cfg)
    return {"ok": True}


@router.post("/channel/stop", summary="停止小模型通道")
async def stop_channel(channel_id: str) -> dict:
    service.stop(channel_id)
    return {"ok": True}


@router.post("/channel/update", summary="更新小模型通道配置")
async def update_channel(cfg: SmallModelChannelConfig) -> dict:
    service.update(cfg)
    return {"ok": True}


@router.get("/channel/status", response_model=SmallModelChannelStatus, summary="查询小模型通道状态")
async def get_status(channel_id: str) -> SmallModelChannelStatus:
    return service.status(channel_id)

