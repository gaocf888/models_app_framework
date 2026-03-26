from __future__ import annotations

from app.models.small_model import SmallModelChannelConfig, SmallModelChannelStatus
from app.small_models.channel_manager import ChannelConfig, ChannelManager


class SmallModelChannelService:
    """
    小模型通道管理服务。

    封装 ChannelManager，对外提供简化的 Pydantic 模型接口。
    """

    def __init__(self, manager: ChannelManager | None = None) -> None:
        self._manager = manager or ChannelManager()

    def start(self, cfg: SmallModelChannelConfig) -> None:
        extra_params = dict(cfg.extra_params or {})
        if cfg.algor_type:
            extra_params["algor_type"] = cfg.algor_type
        if cfg.weights_path:
            extra_params["weights_path"] = cfg.weights_path
        if cfg.callback_url:
            extra_params["callback_url"] = cfg.callback_url
        if cfg.evidence_dir:
            extra_params["evidence_dir"] = cfg.evidence_dir
        if cfg.device:
            extra_params["device"] = cfg.device
        if cfg.imgsz is not None:
            extra_params["imgsz"] = cfg.imgsz
        if cfg.conf is not None:
            extra_params["conf"] = cfg.conf
        if cfg.iou is not None:
            extra_params["iou"] = cfg.iou
        if cfg.cooldown_seconds is not None:
            extra_params["cooldown_seconds"] = cfg.cooldown_seconds
        if cfg.clip_seconds is not None:
            extra_params["clip_seconds"] = cfg.clip_seconds
        self._manager.start_channel(
            cfg.channel_id,
            ChannelConfig(
                model_name=cfg.model_name,
                queue_size=cfg.queue_size,
                video_source=cfg.video_source,
                extra_params=extra_params,
            ),
        )

    def stop(self, channel_id: str) -> None:
        self._manager.stop_channel(channel_id)

    def update(self, cfg: SmallModelChannelConfig) -> None:
        extra_params = dict(cfg.extra_params or {})
        if cfg.algor_type:
            extra_params["algor_type"] = cfg.algor_type
        if cfg.weights_path:
            extra_params["weights_path"] = cfg.weights_path
        if cfg.callback_url:
            extra_params["callback_url"] = cfg.callback_url
        if cfg.evidence_dir:
            extra_params["evidence_dir"] = cfg.evidence_dir
        if cfg.device:
            extra_params["device"] = cfg.device
        if cfg.imgsz is not None:
            extra_params["imgsz"] = cfg.imgsz
        if cfg.conf is not None:
            extra_params["conf"] = cfg.conf
        if cfg.iou is not None:
            extra_params["iou"] = cfg.iou
        if cfg.cooldown_seconds is not None:
            extra_params["cooldown_seconds"] = cfg.cooldown_seconds
        if cfg.clip_seconds is not None:
            extra_params["clip_seconds"] = cfg.clip_seconds
        self._manager.update_channel(
            cfg.channel_id,
            ChannelConfig(
                model_name=cfg.model_name,
                queue_size=cfg.queue_size,
                video_source=cfg.video_source,
                extra_params=extra_params,
            ),
        )

    def status(self, channel_id: str) -> SmallModelChannelStatus:
        raw = self._manager.get_status(channel_id)
        return SmallModelChannelStatus(
            exists=raw.get("exists", False),
            model_name=raw.get("model_name"),
            queue_size=raw.get("queue_size"),
            stopped=raw.get("stopped"),
        )

