from __future__ import annotations

"""
小模型策略包。

目录分层：
- base/：策略接口与 YOLO / InsightFace / 空间规则等基础工具
- l1/：常规目标检测（ObjectDetectionStrategy）
- l2/：常规行为检测（RegularBehaviorDetectionStrategy）
- l3/：复杂行为检测（ComplexBehaviorDetectionStrategy）
- l4/：人脸识别策略（FaceRecognitionStrategy）
- specialized/：独立专项策略（如 CallingDetectionStrategy）
- face/：人脸识别领域模块（库管理、比对、pipeline）
"""

from app.small_models.strategy.base import Detection, SmallModelStrategy, StrategyResult
from app.small_models.strategy.l1 import ObjectDetectionStrategy
from app.small_models.strategy.l2 import RegularBehaviorDetectionStrategy
from app.small_models.strategy.l3 import ComplexBehaviorDetectionStrategy
from app.small_models.strategy.l4 import FaceRecognitionStrategy
from app.small_models.strategy.specialized import CallingDetectionStrategy

__all__ = [
    "CallingDetectionStrategy",
    "ComplexBehaviorDetectionStrategy",
    "Detection",
    "FaceRecognitionStrategy",
    "ObjectDetectionStrategy",
    "RegularBehaviorDetectionStrategy",
    "SmallModelStrategy",
    "StrategyResult",
]
