from __future__ import annotations

from typing import Any, Dict, List

from app.core.logging import get_logger
from app.small_models.algorithm_registry import resolve_path
from app.small_models.strategy.base import Detection, SmallModelStrategy, StrategyResult

logger = get_logger(__name__)


class CallingStrategy(SmallModelStrategy):
    """
    algor_type=40417 接打电话检测策略（Ultralytics YOLO）。

    说明：
    - 该实现不依赖旧工程的 `common/suanfa/utils`；
    - 依赖：`ultralytics`（运行时缺失会抛出明确错误）。
    """

    def __init__(self) -> None:
        self._model = None
        self._weights_path: str | None = None

    def _get_model(self, weights_path: str, device: str | None) -> Any:
        if self._model is not None and self._weights_path == weights_path:
            return self._model
        try:
            from ultralytics import YOLO  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Missing dependency: ultralytics. Install with: pip install ultralytics") from exc

        model = YOLO(weights_path)
        # Ultralytics 的 device 主要在 predict 时传入，这里只缓存 model
        self._model = model
        self._weights_path = weights_path
        logger.info("CallingStrategy loaded weights: %s", weights_path)
        return model

    def infer(self, frame_bgr: Any, *, config: Dict[str, Any]) -> StrategyResult:
        weights_path = resolve_path(str(config.get("weights_path") or ""))
        if not weights_path:
            raise ValueError("CallingStrategy requires weights_path")

        device = config.get("device")
        imgsz = int(config.get("imgsz") or 640)
        conf = float(config.get("conf") or 0.25)
        iou = float(config.get("iou") or 0.7)

        model = self._get_model(weights_path, device)

        # 注意：stream=False 返回 list[Results]
        results = model.predict(source=frame_bgr, imgsz=imgsz, conf=conf, iou=iou, device=device, verbose=False)
        detections: List[Detection] = []
        for r in results:
            if r.boxes is None:
                continue
            for b in r.boxes:
                try:
                    cls_id = int(b.cls[0]) if hasattr(b.cls, "__len__") else int(b.cls)
                    score = float(b.conf[0]) if hasattr(b.conf, "__len__") else float(b.conf)
                    xyxy = b.xyxy[0].tolist() if hasattr(b.xyxy[0], "tolist") else list(b.xyxy[0])
                    x1, y1, x2, y2 = [int(v) for v in xyxy]
                except Exception:
                    continue
                label = str(cls_id)
                detections.append(Detection(label=label, score=score, bbox_xyxy=(x1, y1, x2, y2)))

        triggered = len(detections) > 0
        return StrategyResult(triggered=triggered, detections=detections, extra={"model": "ultralytics_yolo"})