"""
人脸识别策略 — L4（InsightFace 检测 + 对齐 + 1:N 比对）。

与 YOLO 三层策略并列；通过 gallery_id 绑定人脸库。
支持 ROI 过滤、分类型告警（白名单 / 陌生人 / 两者）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.core.logging import get_logger
from app.small_models.strategy.base._insightface_utils import detect_and_embed, draw_faces
from app.small_models.strategy.base.base import Detection, SmallModelStrategy, StrategyResult
from app.small_models.strategy.face.gallery_store import get_face_gallery_store
from app.small_models.strategy.face.pipeline import analyze_faces, filter_faces_by_roi, resolve_face_alert_mode

logger = get_logger(__name__)


def _parse_det_size(raw: Any) -> Tuple[int, int]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return int(raw[0]), int(raw[1])
    return 640, 640


class FaceRecognitionStrategy(SmallModelStrategy):
    """InsightFace 人脸检测 + 人脸库 1:N 识别。"""

    def infer(
        self,
        frame_bgr: Any,
        *,
        config: Dict[str, Any],
        context: Dict[str, Any] | None = None,
    ) -> StrategyResult:
        gallery_id = str(config.get("gallery_id") or "default")
        threshold = float(config.get("match_threshold") or 0.45)
        face_alert_mode = resolve_face_alert_mode(config)
        draw_boxes = bool(config.get("draw_boxes", True))
        model_pack = str(config.get("face_model_pack") or "buffalo_l")
        model_root = config.get("face_model_root")
        det_size = _parse_det_size(config.get("det_size"))
        device = config.get("device")
        det_threshold = float(config.get("conf") or config.get("det_threshold") or 0.5)
        min_face_size = int(config.get("min_face_size") or 40)
        max_faces = int(config.get("max_faces") or 10)
        roi_config = config.get("roi")

        store = get_face_gallery_store(config.get("face_gallery_dir"))
        try:
            gallery_index = store.get_gallery_index(gallery_id)
        except ValueError:
            logger.warning("face gallery not found: %s", gallery_id)
            gallery_index = None

        faces = detect_and_embed(
            frame_bgr,
            model_pack=model_pack,
            model_root=model_root,
            det_size=det_size,
            device=device,
            det_threshold=det_threshold,
            min_face_size=min_face_size,
            max_faces=max_faces,
        )
        faces_in_roi = filter_faces_by_roi(faces, roi_config, frame_bgr.shape)

        det_dicts, draw_items, matches_meta, triggered, alert_types, face_alerts = analyze_faces(
            faces_in_roi,
            gallery_index,
            threshold=threshold,
            face_alert_mode=face_alert_mode,
        )

        detections = [
            Detection(
                label=d["label"],
                score=d["score"],
                bbox_xyxy=d["bbox_xyxy"],
                person_id=d.get("person_id"),
            )
            for d in det_dicts
        ]

        extra: Dict[str, Any] = {
            "algorithm": "face_recognition",
            "gallery_id": gallery_id,
            "match_threshold": threshold,
            "face_alert_mode": face_alert_mode,
            "face_count": len(faces),
            "face_count_in_roi": len(faces_in_roi),
            "roi_applied": bool(roi_config),
            "matches": matches_meta,
            "face_alerts": face_alerts,
            "alert_types": alert_types,
            "insightface": model_pack,
            "index_backend": gallery_index.backend if gallery_index else None,
        }

        if draw_boxes and draw_items:
            try:
                extra["annotated_frame"] = draw_faces(frame_bgr, draw_items)
            except Exception as exc:  # noqa: BLE001
                logger.warning("draw_faces failed: %s", exc)

        return StrategyResult(triggered=triggered, detections=detections, extra=extra)
