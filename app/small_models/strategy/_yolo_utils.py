"""
本包内共用的 Ultralytics YOLO(v8) 加载与检测解析，仅供 strategy 下各算法文件调用。

避免再拆 perception 子包，保持调用链：策略文件 → _yolo_utils → ultralytics。
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Tuple

from app.small_models.strategy.base import Detection

_models: Dict[str, Any] = {}
_lock = threading.Lock()


def get_yolo_model(weights_path: str) -> Any:
    """按权重路径缓存模型实例（同路径多算法类型共用一份内存）。"""
    with _lock:
        if weights_path not in _models:
            try:
                from ultralytics import YOLO  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError("Missing dependency: ultralytics. Install with: pip install ultralytics") from exc
            _models[weights_path] = YOLO(weights_path)
        return _models[weights_path]


def _label_for_class(model: Any, cls_id: int) -> str:
    names = getattr(model, "names", None)
    if isinstance(names, dict) and cls_id in names:
        return str(names[cls_id])
    if isinstance(names, (list, tuple)) and 0 <= cls_id < len(names):
        return str(names[cls_id])
    return str(cls_id)


def predict_detections(
    model: Any,
    frame_bgr: Any,
    *,
    device: Any,
    imgsz: int,
    conf: float,
    iou: float,
) -> List[Detection]:
    """对单帧 BGR 图执行 predict，解析为 Detection 列表。"""
    results = model.predict(
        source=frame_bgr, imgsz=imgsz, conf=conf, iou=iou, device=device, verbose=False
    )
    out: List[Detection] = []
    for r in results:
        if r.boxes is None:
            continue
        for b in r.boxes:
            try:
                cls_id = int(b.cls[0]) if hasattr(b.cls, "__len__") else int(b.cls)
                score = float(b.conf[0]) if hasattr(b.conf, "__len__") else float(b.conf)
                xyxy = b.xyxy[0].tolist() if hasattr(b.xyxy[0], "tolist") else list(b.xyxy[0])
                x1, y1, x2, y2 = [int(v) for v in xyxy]
            except Exception:  # noqa: BLE001
                continue
            out.append(
                Detection(
                    label=_label_for_class(model, cls_id),
                    score=score,
                    bbox_xyxy=(x1, y1, x2, y2),
                    class_id=cls_id,
                )
            )
    return out


def bbox_center_xyxy(bbox: Tuple[int, int, int, int]) -> Tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def point_in_polygon(x: float, y: float, polygon_xy: List[Tuple[float, float]]) -> bool:
    """射线法，不依赖 OpenCV。"""
    if len(polygon_xy) < 3:
        return False
    inside = False
    n = len(polygon_xy)
    for i in range(n):
        x1, y1 = polygon_xy[i]
        x2, y2 = polygon_xy[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1
            if x < xin:
                inside = not inside
    return inside


def segments_cross(
    a1: Tuple[float, float],
    a2: Tuple[float, float],
    b1: Tuple[float, float],
    b2: Tuple[float, float],
) -> bool:
    def orient(p, q, r) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1 = orient(a1, a2, b1)
    o2 = orient(a1, a2, b2)
    o3 = orient(b1, b2, a1)
    o4 = orient(b1, b2, a2)
    return o1 * o2 < 0 and o3 * o4 < 0
