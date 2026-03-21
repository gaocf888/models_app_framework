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
        self._manager.start_channel(
            cfg.channel_id,
            ChannelConfig(
                model_name=cfg.model_name,
                queue_size=cfg.queue_size,
                 # algor_type 作为额外参数传递给算法层，便于按类型选择不同后处理逻辑
                 extra_params={"algor_type": cfg.algor_type} if cfg.algor_type else {},
                video_source=cfg.video_source,
                extra_params=(cfg.extra_params or {}),
            ),
        )

    def stop(self, channel_id: str) -> None:
        self._manager.stop_channel(channel_id)

    def update(self, cfg: SmallModelChannelConfig) -> None:
        self._manager.update_channel(
            cfg.channel_id,
            ChannelConfig(
                model_name=cfg.model_name,
                queue_size=cfg.queue_size,
                extra_params={"algor_type": cfg.algor_type} if cfg.algor_type else {},
                video_source=cfg.video_source,
                extra_params=(cfg.extra_params or {}),
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

