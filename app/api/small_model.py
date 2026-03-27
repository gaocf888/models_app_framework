from __future__ import annotations

"""
小模型通道管理接口。

服务配置前置条件（运维/开发）：
1) 小模型推理依赖可用：
   - 模型权重、推理框架（如 YOLO 依赖）需可加载。
2) 视频源/回调配置可访问：
   - 如传入 video_source / callback_url，需确保网络可达。
3) 证据目录可写：
   - 如传入 evidence_dir，需保证目录可写且磁盘空间充足。
"""

from fastapi import APIRouter

from app.models.small_model import SmallModelChannelConfig, SmallModelChannelStatus
from app.services.small_model_channel_service import SmallModelChannelService

router = APIRouter()
service = SmallModelChannelService()


@router.post("/channel/start", summary="启动小模型通道")
async def start_channel(cfg: SmallModelChannelConfig) -> dict:
    """
    启动小模型通道。

    参数说明（见 SmallModelChannelConfig）：
    - 必传：channel_id
    - 可选：model_name、queue_size、weights_path、callback_url、video_source、extra_params 等
    """
    service.start(cfg)
    return {"ok": True}


@router.post("/channel/stop", summary="停止小模型通道")
async def stop_channel(channel_id: str) -> dict:
    """
    停止指定通道。

    参数说明：
    - 必传：channel_id（query 参数）
    """
    service.stop(channel_id)
    return {"ok": True}


@router.post("/channel/update", summary="更新小模型通道配置")
async def update_channel(cfg: SmallModelChannelConfig) -> dict:
    """
    更新通道配置。

    参数说明（见 SmallModelChannelConfig）：
    - 必传：channel_id
    - 可选：其余配置项按需覆盖
    """
    service.update(cfg)
    return {"ok": True}


@router.get("/channel/status", response_model=SmallModelChannelStatus, summary="查询小模型通道状态")
async def get_status(channel_id: str) -> SmallModelChannelStatus:
    """
    查询通道运行状态。

    参数说明：
    - 必传：channel_id（query 参数）
    """
    return service.status(channel_id)

