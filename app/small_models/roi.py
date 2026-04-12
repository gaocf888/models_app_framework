from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Sequence, Tuple

from app.core.logging import get_logger

logger = get_logger(__name__)


def _box_iou_xyxy(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
    area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def _clip_rect_xyxy(
    x1: int, y1: int, x2: int, y2: int, w: int, h: int
) -> Tuple[int, int, int, int]:
    x1 = max(0, min(x1, w - 1)) if w > 0 else 0
    x2 = max(0, min(x2, w)) if w > 0 else 0
    y1 = max(0, min(y1, h - 1)) if h > 0 else 0
    y2 = max(0, min(y2, h)) if h > 0 else 0
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    return x1, y1, x2, y2


@dataclass
class RoiRuntime:
    """
    像素坐标系下的 ROI，用于过滤检测框。

    - 矩形：支持 center / iou 两种匹配。
    - 多边形：仅支持 center（检测框中心点在多边形内）；若配置为 iou 会降级为 center 并打日志。
    """

    rect_xyxy: Tuple[int, int, int, int] | None
    polygon_xy: Any  # numpy (N,1,2) int32，无 numpy 时为 list
    match_mode: str
    min_iou: float
    frame_w: int
    frame_h: int

    @classmethod
    def from_config(cls, raw: dict[str, Any] | None, frame_h: int, frame_w: int) -> RoiRuntime | None:
        if not raw:
            return None
        try:
            from app.models.small_model import parse_small_model_roi

            cfg = parse_small_model_roi(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("invalid ROI config, ignored: %s", exc)
            return None

        if frame_w <= 0 or frame_h <= 0:
            return None

        match_mode = cfg.match_mode
        min_iou = float(cfg.min_iou)

        if cfg.mode in ("rect", "rect_norm"):
            if cfg.xyxy is None:
                return None
            x1, y1, x2, y2 = (float(v) for v in cfg.xyxy)
            if cfg.mode == "rect_norm":
                x1, x2 = x1 * frame_w, x2 * frame_w
                y1, y2 = y1 * frame_h, y2 * frame_h
            xi1, yi1, xi2, yi2 = _clip_rect_xyxy(
                int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)), frame_w, frame_h
            )
            if xi2 <= xi1 or yi2 <= yi1:
                logger.warning("ROI rect degenerate after clip, ignored")
                return None
            return cls(
                rect_xyxy=(xi1, yi1, xi2, yi2),
                polygon_xy=None,
                match_mode=match_mode,
                min_iou=min_iou,
                frame_w=frame_w,
                frame_h=frame_h,
            )

        # polygon / polygon_norm
        if not cfg.points or len(cfg.points) < 3:
            return None
        import numpy as np

        pts: List[List[int]] = []
        for p in cfg.points:
            if len(p) != 2:
                continue
            a, b = float(p[0]), float(p[1])
            if cfg.mode == "polygon_norm":
                a, b = a * frame_w, b * frame_h
            pts.append([int(round(a)), int(round(b))])
        if len(pts) < 3:
            logger.warning("ROI polygon has too few valid points, ignored")
            return None
        poly = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
        if match_mode == "iou":
            logger.warning("ROI match_mode=iou is not supported for polygon; using center")
            match_mode = "center"
        return cls(
            rect_xyxy=None,
            polygon_xy=poly,
            match_mode=match_mode,
            min_iou=min_iou,
            frame_w=frame_w,
            frame_h=frame_h,
        )

    def box_matches(self, bbox_xyxy: Tuple[int, int, int, int] | None) -> bool:
        if bbox_xyxy is None:
            return False
        x1, y1, x2, y2 = bbox_xyxy
        if self.rect_xyxy is not None:
            rx1, ry1, rx2, ry2 = self.rect_xyxy
            if self.match_mode == "iou":
                return _box_iou_xyxy((x1, y1, x2, y2), (rx1, ry1, rx2, ry2)) >= self.min_iou
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            return rx1 <= cx <= rx2 and ry1 <= cy <= ry2

        if self.polygon_xy is None:
            return True
        import cv2  # type: ignore[import-not-found]

        cx = int(round((x1 + x2) / 2.0))
        cy = int(round((y1 + y2) / 2.0))
        r = cv2.pointPolygonTest(self.polygon_xy, (float(cx), float(cy)), False)
        return r >= 0.0


def filter_detections_by_roi(
    detections: Sequence[Any],
    roi_config: dict[str, Any] | None,
    frame_shape: Tuple[int, ...],
) -> List[Any]:
    """frame_shape: (H, W, ...) from numpy ndarray."""
    if not roi_config or not detections:
        return list(detections)
    h, w = int(frame_shape[0]), int(frame_shape[1])
    rt = RoiRuntime.from_config(roi_config, h, w)
    if rt is None:
        return list(detections)
    out: List[Any] = []
    for d in detections:
        if rt.box_matches(d.bbox_xyxy):
            out.append(d)
    return out
