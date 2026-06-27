from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Tuple

import numpy as np

from app.core.logging import get_logger
from app.models.face_gallery import FaceBatchEnrollItem, FaceIdentifyResponse, FaceMatchItem, FaceVerifyResponse
from app.small_models.strategy.base._insightface_utils import detect_and_embed, draw_faces
from app.small_models.strategy.face.gallery_store import FaceGalleryStore, get_face_gallery_store
from app.small_models.strategy.face.models import new_sample_id
from app.small_models.strategy.face.pipeline import analyze_faces, filter_faces_by_roi, resolve_face_alert_mode

logger = get_logger(__name__)


def _parse_det_size(raw: Any) -> Tuple[int, int]:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return int(raw[0]), int(raw[1])
    return 640, 640


def _load_bgr_from_bytes(data: bytes) -> Any:
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("opencv-python is required") from exc
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("invalid image bytes")
    return img


def _load_bgr_from_path(path: str) -> Any:
    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("opencv-python is required") from exc
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"image not found: {path}")
    img = cv2.imread(str(p))
    if img is None:
        raise ValueError(f"failed to read image: {path}")
    return img


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    va = np.asarray(a, dtype=np.float32)
    vb = np.asarray(b, dtype=np.float32)
    va = va / max(float(np.linalg.norm(va)), 1e-12)
    vb = vb / max(float(np.linalg.norm(vb)), 1e-12)
    return float(np.dot(va, vb))


class FaceGalleryService:
    def __init__(self, store: FaceGalleryStore | None = None) -> None:
        self._store = store or get_face_gallery_store()

    def create_gallery(self, gallery_id: str, *, name: str | None = None, model_pack: str = "buffalo_l") -> dict:
        g = self._store.create_gallery(gallery_id, name=name, model_pack=model_pack)
        return {"gallery_id": g.gallery_id, "name": g.name, "model_pack": g.model_pack}

    def list_galleries(self) -> list[dict]:
        return self._store.list_galleries()

    def delete_gallery(self, gallery_id: str) -> bool:
        return self._store.delete_gallery(gallery_id)

    def gallery_stats(self, gallery_id: str) -> dict:
        return self._store.gallery_index_stats(gallery_id)

    def list_persons(self, gallery_id: str) -> list[dict]:
        return self._store.list_persons(gallery_id)

    def delete_person(self, gallery_id: str, person_id: str) -> bool:
        return self._store.delete_person(gallery_id, person_id)

    def enroll_image_bytes(
        self,
        gallery_id: str,
        *,
        person_id: str,
        name: str | None,
        image_bytes: bytes,
        metadata: dict | None = None,
        model_pack: str | None = None,
        model_root: str | None = None,
        device: str | None = None,
        det_size: Any = None,
        save_image: bool = True,
    ) -> dict:
        gallery = self._store.get_gallery(gallery_id)
        if gallery is None:
            raise ValueError(f"gallery not found: {gallery_id}")

        pack = model_pack or gallery.model_pack
        frame = _load_bgr_from_bytes(image_bytes)
        faces = detect_and_embed(
            frame,
            model_pack=pack,
            model_root=model_root,
            det_size=_parse_det_size(det_size),
            device=device,
            max_faces=1,
        )
        if not faces:
            raise ValueError("no face detected in image")

        face = faces[0]
        sid = new_sample_id()
        source_path = None
        if save_image:
            source_path = self._store.save_enroll_image(gallery_id, person_id, sid, image_bytes)

        sample = self._store.enroll(
            gallery_id,
            person_id=person_id,
            name=name,
            embedding=face.embedding,
            source_image_path=source_path,
            metadata=metadata,
            sample_id=sid,
        )
        person = self._store.get_gallery(gallery_id)
        display_name = name or person_id
        if person and person_id in person.persons:
            display_name = person.persons[person_id].name

        return {
            "gallery_id": gallery_id,
            "person_id": person_id,
            "name": display_name,
            "sample_id": sample.sample_id,
            "source_image": sample.source_image,
        }

    def enroll_image_path(
        self,
        gallery_id: str,
        *,
        person_id: str,
        name: str | None,
        image_path: str,
        metadata: dict | None = None,
        model_pack: str | None = None,
        model_root: str | None = None,
        device: str | None = None,
        det_size: Any = None,
    ) -> dict:
        data = Path(image_path).read_bytes()
        return self.enroll_image_bytes(
            gallery_id,
            person_id=person_id,
            name=name,
            image_bytes=data,
            metadata=metadata,
            model_pack=model_pack,
            model_root=model_root,
            device=device,
            det_size=det_size,
            save_image=True,
        )

    def batch_enroll(
        self,
        gallery_id: str,
        items: List[FaceBatchEnrollItem],
        *,
        model_pack: str | None = None,
        model_root: str | None = None,
        device: str | None = None,
        det_size: Any = None,
    ) -> dict:
        ok: list[dict] = []
        failed: list[dict] = []
        for item in items:
            try:
                r = self.enroll_image_path(
                    gallery_id,
                    person_id=item.person_id,
                    name=item.name,
                    image_path=item.image_path,
                    model_pack=model_pack,
                    model_root=model_root,
                    device=device,
                    det_size=det_size,
                )
                ok.append(r)
            except Exception as exc:  # noqa: BLE001
                failed.append({"person_id": item.person_id, "image_path": item.image_path, "error": str(exc)})
        return {"enrolled": ok, "failed": failed}

    def identify_image_bytes(
        self,
        gallery_id: str,
        image_bytes: bytes,
        *,
        threshold: float = 0.45,
        model_pack: str | None = None,
        model_root: str | None = None,
        device: str | None = None,
        det_size: Any = None,
        unknown_alert: bool = False,
        face_alert_mode: str | None = None,
        roi: dict | None = None,
        save_annotated_dir: str | None = None,
    ) -> FaceIdentifyResponse:
        gallery = self._store.get_gallery(gallery_id)
        if gallery is None:
            raise ValueError(f"gallery not found: {gallery_id}")

        cfg = {
            "unknown_alert": unknown_alert,
            "face_alert_mode": face_alert_mode,
        }
        alert_mode = resolve_face_alert_mode(cfg)

        pack = model_pack or gallery.model_pack
        frame = _load_bgr_from_bytes(image_bytes)
        gallery_index = self._store.get_gallery_index(gallery_id)
        faces = detect_and_embed(
            frame,
            model_pack=pack,
            model_root=model_root,
            det_size=_parse_det_size(det_size),
            device=device,
        )
        faces_in_roi = filter_faces_by_roi(faces, roi, frame.shape)

        _, draw_items, matches_meta, _, alert_types, face_alerts = analyze_faces(
            faces_in_roi,
            gallery_index,
            threshold=threshold,
            face_alert_mode=alert_mode,
        )

        def _to_item(m: dict) -> FaceMatchItem:
            return FaceMatchItem(
                bbox_xyxy=list(m["bbox_xyxy"]) if m.get("bbox_xyxy") else None,
                person_id=m.get("person_id"),
                person_name=m.get("person_name"),
                match_type=str(m["match_type"]),
                similarity=m.get("similarity"),
                det_score=m.get("det_score"),
                label=m.get("person_name") or ("unknown" if m["match_type"] == "unknown" else None),
                alert=bool(m.get("alert")),
            )

        matches = [_to_item(m) for m in matches_meta]
        alerts = [_to_item(m) for m in face_alerts]

        annotated_path = None
        if save_annotated_dir and draw_items:
            out_dir = Path(save_annotated_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            from time import time

            annotated = draw_faces(frame, draw_items)
            try:
                import cv2  # type: ignore[import-not-found]
            except ImportError:
                cv2 = None  # type: ignore[assignment]
            if cv2 is not None:
                annotated_path = str((out_dir / f"identify_{int(time() * 1000)}.jpg").resolve())
                cv2.imwrite(annotated_path, annotated)

        return FaceIdentifyResponse(
            gallery_id=gallery_id,
            face_count=len(faces),
            face_count_in_roi=len(faces_in_roi),
            matches=matches,
            alert_types=alert_types,
            face_alerts=alerts,
            annotated_image_path=annotated_path,
        )

    def verify_images_bytes(
        self,
        image_a: bytes,
        image_b: bytes,
        *,
        threshold: float = 0.45,
        model_pack: str = "buffalo_l",
        model_root: str | None = None,
        device: str | None = None,
        det_size: Any = None,
    ) -> FaceVerifyResponse:
        fa = _load_bgr_from_bytes(image_a)
        fb = _load_bgr_from_bytes(image_b)
        size = _parse_det_size(det_size)
        faces_a = detect_and_embed(fa, model_pack=model_pack, model_root=model_root, det_size=size, device=device, max_faces=1)
        faces_b = detect_and_embed(fb, model_pack=model_pack, model_root=model_root, det_size=size, device=device, max_faces=1)
        if not faces_a or not faces_b:
            raise ValueError("face not detected in one or both images")
        sim = _cosine_similarity(faces_a[0].embedding, faces_b[0].embedding)
        return FaceVerifyResponse(verified=sim >= threshold, similarity=sim, threshold=threshold)

    def verify_person_image(
        self,
        gallery_id: str,
        person_id: str,
        image_bytes: bytes,
        *,
        threshold: float = 0.45,
        model_pack: str | None = None,
        model_root: str | None = None,
        device: str | None = None,
        det_size: Any = None,
    ) -> FaceVerifyResponse:
        gallery = self._store.get_gallery(gallery_id)
        if gallery is None:
            raise ValueError(f"gallery not found: {gallery_id}")
        person = gallery.persons.get(person_id)
        if person is None or not person.samples:
            raise ValueError(f"person not found or no samples: {person_id}")

        pack = model_pack or gallery.model_pack
        frame = _load_bgr_from_bytes(image_bytes)
        faces = detect_and_embed(
            frame,
            model_pack=pack,
            model_root=model_root,
            det_size=_parse_det_size(det_size),
            device=device,
            max_faces=1,
        )
        if not faces:
            raise ValueError("no face detected in image")

        best_sim = max(_cosine_similarity(faces[0].embedding, s.embedding) for s in person.samples)
        return FaceVerifyResponse(verified=best_sim >= threshold, similarity=best_sim, threshold=threshold)
