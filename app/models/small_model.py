from __future__ import annotations

from typing import Literal

import pydantic
from pydantic import BaseModel, Field

_PYDANTIC_V2 = int(pydantic.VERSION.split(".", maxsplit=1)[0]) >= 2
if _PYDANTIC_V2:
    from pydantic import model_validator
else:
    from pydantic import root_validator  # type: ignore[attr-defined,no-redef]


class SmallModelRoiConfig(BaseModel):
    """
    检测 ROI。坐标系：像素模式相对整帧左上角；归一化模式为 [0,1] 乘以帧宽高。

    - rect / rect_norm: 轴对齐矩形 xyxy。
    - polygon / polygon_norm: 多边形顶点序列（至少 3 点）；匹配方式仅为「检测框中心在多边形内」。
    - match_mode=iou 仅对矩形 ROI 有效；多边形会降级为 center。
    """

    mode: Literal["rect", "rect_norm", "polygon", "polygon_norm"] = Field(
        ...,
        description="矩形或多边形；带 _norm 后缀表示 0~1 归一化坐标",
    )
    xyxy: tuple[float, float, float, float] | None = Field(
        None,
        description="矩形 [x1,y1,x2,y2]",
    )
    points: list[tuple[float, float]] | None = Field(
        None,
        description="多边形顶点 [[x,y], ...]，至少 3 点",
    )
    match_mode: Literal["center", "iou"] = Field(
        "center",
        description="center：框中心在 ROI 内；iou：与矩形 ROI 的 IoU≥min_iou",
    )
    min_iou: float = Field(0.01, ge=0.0, le=1.0, description="match_mode=iou 时生效")

    if _PYDANTIC_V2:

        @model_validator(mode="after")
        def _validate_geometry(self) -> SmallModelRoiConfig:
            if self.mode in ("rect", "rect_norm"):
                if self.xyxy is None:
                    raise ValueError("ROI mode rect/rect_norm requires xyxy")
            if self.mode in ("polygon", "polygon_norm"):
                if not self.points or len(self.points) < 3:
                    raise ValueError("ROI polygon modes require at least 3 points")
            return self

    else:

        @root_validator
        def _validate_geometry(cls, values: dict) -> dict:  # type: ignore[misc]
            mode = values.get("mode")
            if mode in ("rect", "rect_norm"):
                if values.get("xyxy") is None:
                    raise ValueError("ROI mode rect/rect_norm requires xyxy")
            if mode in ("polygon", "polygon_norm"):
                pts = values.get("points")
                if not pts or len(pts) < 3:
                    raise ValueError("ROI polygon modes require at least 3 points")
            return values


def parse_small_model_roi(raw: dict | SmallModelRoiConfig) -> SmallModelRoiConfig:
    if isinstance(raw, SmallModelRoiConfig):
        return raw
    if _PYDANTIC_V2:
        return SmallModelRoiConfig.model_validate(raw)
    return SmallModelRoiConfig.parse_obj(raw)


def serialize_small_model_roi(roi: SmallModelRoiConfig) -> dict:
    if _PYDANTIC_V2:
        return roi.model_dump(mode="json")
    return roi.dict()


class SmallModelChannelConfig(BaseModel):
    channel_id: str = Field(..., description="通道唯一标识")
    model_name: str = Field(
        "yolo-placeholder",
        description="展示/指标用名称；推理以 algor_type 为主，可与 YAML 中 model_name 对齐",
    )
    queue_size: int = Field(64, description="内部消息队列容量")
    algor_type: str | None = Field(
        None,
        description="必填（生产）：与 configs/small_model_algorithms.yaml 中键一致，如 40111、40417、42101",
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
    roi: SmallModelRoiConfig | None = Field(
        None,
        description="检测感兴趣区域；与 extra_params.roi 同时存在时，以本字段为准（服务层会覆盖写入 extra_params）",
    )
    class_filter: dict | None = Field(
        None,
        description="可选；class_ids / class_names，覆盖 YAML 中 class_filter",
    )
    complex_mode: str | None = Field(
        None,
        description="L3 可选：none | dwell | line_cross | zone_intrusion",
    )
    dwell_seconds: float | None = Field(None, description="L3 dwell 滞留秒数，覆盖 YAML")
    dwell_polygon: list[list[float]] | None = Field(
        None,
        description="L3 dwell 多边形顶点 [[x,y],...]，覆盖 YAML",
    )
    line_cross_line: list[list[float]] | None = Field(
        None,
        description="L3 绊线两端点 [[x1,y1],[x2,y2]]，覆盖 YAML",
    )
    zone_intrusion_polygon: list[list[float]] | None = Field(
        None,
        description="L3 禁区多边形顶点，覆盖 YAML",
    )
    extra_params: dict | None = Field(
        None,
        description="其余覆盖项（JSON）；与顶层同名字段同时存在时，服务层以顶层字段为准写入 extra_params",
    )


class SmallModelChannelStatus(BaseModel):
    exists: bool = Field(..., description="通道是否存在")
    model_name: str | None = Field(None, description="当前模型名称")
    queue_size: int | None = Field(None, description="消息队列当前长度")
    stopped: bool | None = Field(None, description="通道是否已停止")

