"""接打电话等场景：跨目标空间关系规则（与 YOLO 检测框配合）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from app.small_models.strategy._yolo_utils import bbox_center_xyxy
from app.small_models.strategy.base import Detection


@dataclass(frozen=True)
class CallingSpatialMatch:
    """一人与一手机的空间匹配结果。"""

    person: Detection
    phone: Detection
    match_score: float


def phone_center_in_person_upper_body(
    person_bbox: Tuple[int, int, int, int],
    phone_bbox: Tuple[int, int, int, int],
    *,
    upper_body_ratio: float = 0.45,
) -> bool:
    """
    手机框中心点是否落在行人框上半身区域（ym CallingStrategyV2 先验）。
    upper_body_ratio: 从 person 顶边向下占框高的比例（默认 0.45）。
    """
    px1, py1, px2, py2 = person_bbox
    fx1, fy1, fx2, fy2 = phone_bbox
    ph = max(0, py2 - py1)
    if ph <= 0:
        return False
    upper_ymax = py1 + ph * max(0.05, min(1.0, upper_body_ratio))
    cx, cy = bbox_center_xyxy((fx1, fy1, fx2, fy2))
    return px1 <= cx <= px2 and py1 <= cy <= upper_ymax


def match_calling_by_phone_spatial(
    persons: Sequence[Detection],
    phones: Sequence[Detection],
    *,
    upper_body_ratio: float = 0.45,
    min_phone_conf: float = 0.5,
) -> List[CallingSpatialMatch]:
    """
    对每个 person，在其上半身区域内寻找置信度最高的 phone。
    仅当存在合法 phone 匹配时判定该 person 为接打电话。
    """
    matches: List[CallingSpatialMatch] = []
    for person in persons:
        if not person.bbox_xyxy:
            continue
        best_phone: Detection | None = None
        best_score: float | None = None
        for phone in phones:
            if not phone.bbox_xyxy:
                continue
            if phone.score < min_phone_conf:
                continue
            if phone_center_in_person_upper_body(
                person.bbox_xyxy,
                phone.bbox_xyxy,
                upper_body_ratio=upper_body_ratio,
            ):
                if best_score is None or phone.score > best_score:
                    best_score = phone.score
                    best_phone = phone
        if best_phone is not None and best_score is not None:
            matches.append(
                CallingSpatialMatch(person=person, phone=best_phone, match_score=best_score)
            )
    return matches
