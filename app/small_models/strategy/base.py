from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    bbox_xyxy: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class StrategyResult:
    triggered: bool
    detections: List[Detection]
    extra: Dict[str, Any]


class SmallModelStrategy:
    """
    每种算法类型（algor_type）对应一个 Strategy 实现。
    """

    def infer(self, frame_bgr: Any, *, config: Dict[str, Any]) -> StrategyResult:  # frame: np.ndarray
        raise NotImplementedError

