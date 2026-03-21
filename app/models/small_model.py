from __future__ import annotations

from pydantic import BaseModel, Field


class SmallModelChannelConfig(BaseModel):
    channel_id: str = Field(..., description="通道唯一标识")
    model_name: str = Field("yolo-placeholder", description="使用的小模型名称/算法标识")
    queue_size: int = Field(64, description="内部消息队列容量")
    algor_type: str | None = Field(
        None,
        description="算法类型（如 helmet/phone_call 等，可与 SmallModelRegistry 中的 task_type 对应）",
    )
    video_source: str | None = Field(
        None,
        description="视频流/视频文件源地址，例如 rtsp://... 或 /path/to/file.mp4；示例环境可为空，生产环境建议必填",
    )
    extra_params: dict | None = Field(
        None,
        description="与该通道算法相关的其他配置（JSON 对象，如阈值、ROI 等），由上层以配置化方式传入",
    )


class SmallModelChannelStatus(BaseModel):
    exists: bool = Field(..., description="通道是否存在")
    model_name: str | None = Field(None, description="当前模型名称")
    queue_size: int | None = Field(None, description="消息队列当前长度")
    stopped: bool | None = Field(None, description="通道是否已停止")

