from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class Detection:
    """单目标检测结果；class_id 由 YOLO 解析填充，供 class_filter 使用。"""

    label: str
    score: float
    bbox_xyxy: tuple[int, int, int, int] | None = None
    class_id: int | None = None
    track_id: int | None = None


@dataclass(frozen=True)
class StrategyResult:
    """策略输出；extra 可放 algorithm、complex_mode 等调试字段。"""

    triggered: bool
    detections: List[Detection]
    extra: Dict[str, Any]


class SmallModelStrategy:
    """每种 YAML strategy 名对应一个子类；context 含 channel_id、algor_type（复杂行为用）。"""

    def infer(
        self,
        frame_bgr: Any,
        *,
        config: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> StrategyResult:
        raise NotImplementedError
