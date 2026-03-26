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
    weights_path: str | None = Field(
        None,
        description="模型权重路径（可选；若传入则覆盖本地配置/注册表配置）",
    )
    callback_url: str | None = Field(
        None,
        description="检测结果回调地址（可选；若传入则覆盖本地配置）",
    )
    evidence_dir: str | None = Field(
        None,
        description="证据（图片/视频）保存目录（可选；若传入则覆盖本地配置）",
    )
    device: str | None = Field(
        None,
        description="推理设备（可选；如 '0'/'cpu'；若传入则覆盖本地配置）",
    )
    imgsz: int | None = Field(
        None,
        description="推理输入尺寸（可选；若传入则覆盖本地配置）",
    )
    conf: float | None = Field(
        None,
        description="置信度阈值（可选；若传入则覆盖本地配置）",
    )
    iou: float | None = Field(
        None,
        description="NMS IoU 阈值（可选；若传入则覆盖本地配置）",
    )
    cooldown_seconds: int | None = Field(
        None,
        description="同类告警冷却时间（秒）（可选；若传入则覆盖本地配置）",
    )
    clip_seconds: int | None = Field(
        None,
        description="保存视频片段时长（秒）（可选；若传入则覆盖本地配置）",
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

