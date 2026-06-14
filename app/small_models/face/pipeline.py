from __future__ import annotations

from typing import Any, Dict, List, Tuple

from app.small_models.face.gallery_index import GalleryIndex
from app.small_models.roi import RoiRuntime
from app.small_models.strategy._insightface_utils import FaceInsight


def resolve_face_alert_mode(config: Dict[str, Any]) -> str:
    """
    告警模式：
    - identified：仅白名单命中告警
    - unknown：仅陌生人告警
    - both：两者均告警
    未配置 face_alert_mode 时：unknown_alert=true → both，否则 identified。
    """
    raw = config.get("face_alert_mode")
    if raw:
        mode = str(raw).lower().strip()
        if mode in ("identified", "unknown", "both"):
            return mode
    return "both" if bool(config.get("unknown_alert", False)) else "identified"


def filter_faces_by_roi(
    faces: List[FaceInsight],
    roi_config: dict[str, Any] | None,
    frame_shape: Tuple[int, ...],
) -> List[FaceInsight]:
    if not roi_config or not faces:
        return list(faces)
    h, w = int(frame_shape[0]), int(frame_shape[1])
    rt = RoiRuntime.from_config(roi_config, h, w)
    if rt is None:
        return list(faces)
    return [f for f in faces if rt.box_matches(f.bbox_xyxy)]


def analyze_faces(
    faces: List[FaceInsight],
    gallery_index: GalleryIndex | None,
    *,
    threshold: float,
    face_alert_mode: str,
) -> tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    bool,
    List[str],
    List[Dict[str, Any]],
]:
    """返回 detections, draw_items, matches_meta, triggered, alert_types, face_alerts。"""
    detections: List[Dict[str, Any]] = []
    draw_items: List[Dict[str, Any]] = []
    matches_meta: List[Dict[str, Any]] = []
    alert_types: set[str] = set()

    for face in faces:
        match = gallery_index.search(face.embedding, threshold=threshold) if gallery_index else None
        if match is not None:
            label = match.person_name
            score = match.score
            person_id = match.person_id
            match_type = "identified"
            color = (0, 255, 0)
            will_alert = face_alert_mode in ("identified", "both")
            if will_alert:
                alert_types.add("identified")
        else:
            label = "unknown"
            score = face.det_score
            person_id = None
            match_type = "unknown"
            color = (0, 0, 255)
            will_alert = face_alert_mode in ("unknown", "both")
            if will_alert:
                alert_types.add("unknown")

        detections.append(
            {
                "label": label,
                "score": float(score),
                "bbox_xyxy": face.bbox_xyxy,
                "person_id": person_id,
            }
        )
        draw_items.append(
            {
                "bbox_xyxy": face.bbox_xyxy,
                "label": label,
                "score": score if match is not None else face.det_score,
                "color": color,
            }
        )
        matches_meta.append(
            {
                "bbox_xyxy": face.bbox_xyxy,
                "person_id": person_id,
                "person_name": label if match else None,
                "match_type": match_type,
                "similarity": float(score) if match else None,
                "det_score": face.det_score,
                "alert": will_alert,
            }
        )

    triggered = bool(alert_types)
    face_alerts = [m for m in matches_meta if m.get("alert")]
    return detections, draw_items, matches_meta, triggered, sorted(alert_types), face_alerts
