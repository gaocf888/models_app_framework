"""
常规行为检测（打架、接打电话、口罩等）— 企业级分层中的 L2。

与 L1「常规目标检测」技术路径相同：YOLOv8 + YAML 区分场景；文件独立便于治理与审计。
区别仅在于业务语义与默认阈值/类别过滤；请在 YAML 中为每种行为单独 algor_type，
配置专用自训练权重或官方模型 + class_filter。

策略类名：RegularBehaviorDetectionStrategy
"""

from __future__ import annotations

from typing import Any, Dict

from app.small_models.strategy.base import SmallModelStrategy, StrategyResult
from app.small_models.strategy.object_detection import run_yolo_detection_pipeline


class RegularBehaviorDetectionStrategy(SmallModelStrategy):
    """常规行为类检测（底层与 ObjectDetectionStrategy 一致）。"""

    def infer(
        self,
        frame_bgr: Any,
        *,
        config: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> StrategyResult:
        dets = run_yolo_detection_pipeline(frame_bgr, config)
        return StrategyResult(
            triggered=len(dets) > 0,
            detections=dets,
            extra={
                "algorithm": "regular_behavior_detection",
                "yolo": "ultralytics",
            },
        )
