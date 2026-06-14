"""
InsightFace 封装：检测、对齐、特征提取、画框。

FaceAnalysis.get() 内部完成检测 + 5 点对齐 + ArcFace embedding。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from app.core.logging import get_logger
from app.small_models.algorithm_registry import resolve_path

logger = get_logger(__name__)

_apps: Dict[str, Any] = {}
_apps_lock = threading.Lock()


@dataclass(frozen=True)
class FaceInsight:
    bbox_xyxy: Tuple[int, int, int, int]
    det_score: float
    embedding: List[float]
    kps: List[List[float]] | None = None


def _parse_device(device: str | None) -> int:
    if not device:
        return -1
    d = str(device).strip().lower()
    if d in ("cpu", "-1"):
        return -1
    if d.isdigit():
        return int(d)
    if d.startswith("cuda:"):
        tail = d.split(":", 1)[1]
        return int(tail) if tail.isdigit() else 0
    return 0


def _app_cache_key(model_pack: str, root: str, det_size: Tuple[int, int], ctx_id: int) -> str:
    return f"{model_pack}|{root}|{det_size[0]}x{det_size[1]}|ctx{ctx_id}"


def get_face_app(
    *,
    model_pack: str = "buffalo_l",
    model_root: str | None = None,
    det_size: Tuple[int, int] = (640, 640),
    device: str | None = None,
) -> Any:
    root = resolve_path(model_root or "app/small_models/pretrained/insightface") or "app/small_models/pretrained/insightface"
    ctx_id = _parse_device(device)
    key = _app_cache_key(model_pack, root, det_size, ctx_id)

    with _apps_lock:
        if key in _apps:
            return _apps[key]

        try:
            from insightface.app import FaceAnalysis  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: insightface. Install with: pip install insightface onnxruntime"
            ) from exc

        providers = ["CPUExecutionProvider"]
        if ctx_id >= 0:
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        app = FaceAnalysis(name=model_pack, root=root, providers=providers)
        app.prepare(ctx_id=ctx_id, det_size=det_size)
        _apps[key] = app
        logger.info("insightface loaded model_pack=%s root=%s det_size=%s ctx_id=%s", model_pack, root, det_size, ctx_id)
        return app


def _bbox_from_face(face: Any) -> Tuple[int, int, int, int]:
    bb = face.bbox
    x1, y1, x2, y2 = [int(v) for v in bb[:4]]
    return x1, y1, x2, y2


def _embedding_from_face(face: Any) -> List[float]:
    emb = getattr(face, "normed_embedding", None)
    if emb is None:
        emb = face.embedding
    return [float(x) for x in emb]


def detect_and_embed(
    frame_bgr: Any,
    *,
    model_pack: str = "buffalo_l",
    model_root: str | None = None,
    det_size: Tuple[int, int] = (640, 640),
    device: str | None = None,
    det_threshold: float = 0.5,
    min_face_size: int = 40,
    max_faces: int = 10,
) -> List[FaceInsight]:
    app = get_face_app(
        model_pack=model_pack,
        model_root=model_root,
        det_size=det_size,
        device=device,
    )
    faces = app.get(frame_bgr)
    out: List[FaceInsight] = []

    for face in faces:
        score = float(getattr(face, "det_score", 0.0) or 0.0)
        if score < det_threshold:
            continue
        bbox = _bbox_from_face(face)
        w = max(0, bbox[2] - bbox[0])
        h = max(0, bbox[3] - bbox[1])
        if min(w, h) < min_face_size:
            continue
        kps = None
        if getattr(face, "kps", None) is not None:
            kps = [[float(x), float(y)] for x, y in face.kps]
        out.append(
            FaceInsight(
                bbox_xyxy=bbox,
                det_score=score,
                embedding=_embedding_from_face(face),
                kps=kps,
            )
        )

    out.sort(key=lambda f: (f.bbox_xyxy[2] - f.bbox_xyxy[0]) * (f.bbox_xyxy[3] - f.bbox_xyxy[1]), reverse=True)
    return out[: max(1, max_faces)] if max_faces > 0 else out


def draw_faces(
    frame_bgr: Any,
    items: List[Dict[str, Any]],
) -> Any:
    """items: bbox_xyxy, label, score, color(optional BGR tuple)."""
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("opencv-python is required for draw_faces") from exc

    img = frame_bgr.copy() if hasattr(frame_bgr, "copy") else np.array(frame_bgr, copy=True)
    for it in items:
        bb = it.get("bbox_xyxy")
        if not bb:
            continue
        x1, y1, x2, y2 = [int(v) for v in bb]
        color = it.get("color") or (0, 255, 0)
        label = str(it.get("label") or "")
        score = it.get("score")
        if score is not None:
            label = f"{label} {float(score):.2f}".strip()
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
        if label:
            cv2.putText(img, label, (x1, max(0, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    return img
