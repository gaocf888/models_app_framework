"""
常规目标检测（安全帽、灭火器、车辆等）— 企业级分层中的 L1。

同一套 YOLOv8 检测逻辑；不同场景仅通过 small_model_algorithms.yaml 中 algor_type 条目区分：
- weights_path：官方预训练 yolov8*.pt 或自训练权重
- conf / imgsz / roi / class_filter：按场景调参

策略类名：ObjectDetectionStrategy（YAML 中 strategy 字段填写此名）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from app.small_models.algorithm_registry import resolve_path
from app.small_models.roi import filter_detections_by_roi
from app.small_models.strategy._yolo_utils import get_yolo_model, predict_detections
from app.small_models.strategy.base import Detection, SmallModelStrategy, StrategyResult


def apply_class_filter(detections: List[Detection], class_filter: Optional[dict]) -> List[Detection]:
    """
    class_filter 示例：
      class_ids: [0, 1]
      class_names: ["helmet", "person"]
    二者任一命中即保留（若仅配一类则只按该类过滤）。
    """
    if not class_filter:
        return list(detections)
    ids: Set[int] = set(class_filter.get("class_ids") or [])
    names = {str(x).lower() for x in (class_filter.get("class_names") or [])}
    if not ids and not names:
        return list(detections)
    out: List[Detection] = []
    for d in detections:
        ok_id = d.class_id is not None and d.class_id in ids
        ok_name = d.label.lower() in names if names else False
        if ids and names:
            if ok_id or ok_name:
                out.append(d)
        elif ids:
            if ok_id:
                out.append(d)
        else:
            if ok_name:
                out.append(d)
    return out


def run_yolo_detection_pipeline(frame_bgr: Any, config: Dict[str, Any]) -> List[Detection]:
    """标准路径：解析权重 → YOLO → 类过滤 → ROI。"""
    wp = resolve_path(str(config.get("weights_path") or ""))
    if not wp:
        raise ValueError("weights_path is required for object/behavior detection")

    model = get_yolo_model(wp)
    device = config.get("device")
    imgsz = int(config.get("imgsz") or 640)
    conf = float(config.get("conf") or 0.25)
    iou = float(config.get("iou") or 0.7)

    dets = predict_detections(model, frame_bgr, device=device, imgsz=imgsz, conf=conf, iou=iou)
    dets = apply_class_filter(dets, config.get("class_filter"))
    roi_cfg = config.get("roi")
    if roi_cfg:
        dets = filter_detections_by_roi(dets, roi_cfg, frame_bgr.shape)
    return dets


class ObjectDetectionStrategy(SmallModelStrategy):
    """常规目标检测策略。"""

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
                "algorithm": "object_detection",
                "yolo": "ultralytics",
            },
        )
