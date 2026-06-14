from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List

from app.core.logging import get_logger
from app.small_models.algorithm_registry import resolve_path
from app.small_models.face.models import FaceGallery, FacePerson, FaceSample, new_sample_id

logger = get_logger(__name__)

_store_singleton: FaceGalleryStore | None = None
_store_lock = threading.Lock()
_index_cache: dict[str, tuple[str, Any]] = {}


class FaceGalleryStore:
    """人脸库持久化（JSON）；线程安全读写。"""

    def __init__(self, base_dir: str | None = None) -> None:
        root = resolve_path(base_dir or "data/face_galleries") or "data/face_galleries"
        self._base_dir = Path(root)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _gallery_path(self, gallery_id: str) -> Path:
        return self._base_dir / gallery_id / "gallery.json"

    def _gallery_images_dir(self, gallery_id: str) -> Path:
        d = self._base_dir / gallery_id / "images"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_gallery_unlocked(self, gallery_id: str) -> FaceGallery | None:
        path = self._gallery_path(gallery_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return FaceGallery.from_dict(data)

    def _save_gallery_unlocked(self, gallery: FaceGallery) -> None:
        path = self._gallery_path(gallery.gallery_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(gallery.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        _index_cache.pop(gallery.gallery_id, None)

    def list_galleries(self) -> List[Dict[str, Any]]:
        with self._lock:
            out: List[Dict[str, Any]] = []
            if not self._base_dir.exists():
                return out
            for d in sorted(self._base_dir.iterdir()):
                if not d.is_dir():
                    continue
                g = self._load_gallery_unlocked(d.name)
                if g is None:
                    continue
                out.append(
                    {
                        "gallery_id": g.gallery_id,
                        "name": g.name,
                        "model_pack": g.model_pack,
                        "person_count": len(g.persons),
                        "sample_count": sum(len(p.samples) for p in g.persons.values()),
                        "created_at": g.created_at,
                        "updated_at": g.updated_at,
                    }
                )
            return out

    def create_gallery(self, gallery_id: str, *, name: str | None = None, model_pack: str = "buffalo_l") -> FaceGallery:
        with self._lock:
            if self._load_gallery_unlocked(gallery_id) is not None:
                raise ValueError(f"gallery already exists: {gallery_id}")
            gallery = FaceGallery(gallery_id=gallery_id, name=name or gallery_id, model_pack=model_pack)
            self._save_gallery_unlocked(gallery)
            logger.info("face gallery created: %s", gallery_id)
            return gallery

    def get_gallery(self, gallery_id: str) -> FaceGallery | None:
        with self._lock:
            return self._load_gallery_unlocked(gallery_id)

    def delete_gallery(self, gallery_id: str) -> bool:
        with self._lock:
            path = self._gallery_path(gallery_id)
            if not path.exists():
                return False
            gallery_dir = path.parent
            path.unlink()
            for child in gallery_dir.iterdir():
                if child.is_file():
                    child.unlink()
            try:
                gallery_dir.rmdir()
            except OSError:
                pass
            logger.info("face gallery deleted: %s", gallery_id)
            _index_cache.pop(gallery_id, None)
            return True

    def list_persons(self, gallery_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            gallery = self._load_gallery_unlocked(gallery_id)
            if gallery is None:
                raise ValueError(f"gallery not found: {gallery_id}")
            return [
                {
                    "person_id": p.person_id,
                    "name": p.name,
                    "metadata": dict(p.metadata),
                    "sample_count": len(p.samples),
                }
                for p in gallery.persons.values()
            ]

    def delete_person(self, gallery_id: str, person_id: str) -> bool:
        with self._lock:
            gallery = self._load_gallery_unlocked(gallery_id)
            if gallery is None:
                raise ValueError(f"gallery not found: {gallery_id}")
            if person_id not in gallery.persons:
                return False
            del gallery.persons[person_id]
            gallery.touch()
            self._save_gallery_unlocked(gallery)
            return True

    def enroll(
        self,
        gallery_id: str,
        *,
        person_id: str,
        name: str | None,
        embedding: List[float],
        source_image_path: str | None = None,
        metadata: Dict[str, Any] | None = None,
        sample_id: str | None = None,
    ) -> FaceSample:
        with self._lock:
            gallery = self._load_gallery_unlocked(gallery_id)
            if gallery is None:
                raise ValueError(f"gallery not found: {gallery_id}")

            person = gallery.persons.get(person_id)
            if person is None:
                person = FacePerson(
                    person_id=person_id,
                    name=name or person_id,
                    metadata=dict(metadata or {}),
                )
                gallery.persons[person_id] = person
            else:
                if name:
                    person.name = name
                if metadata:
                    person.metadata.update(metadata)

            sample = FaceSample(
                sample_id=sample_id or new_sample_id(),
                person_id=person_id,
                embedding=[float(x) for x in embedding],
                source_image=source_image_path,
            )
            person.samples.append(sample)
            gallery.touch()
            self._save_gallery_unlocked(gallery)
            return sample

    def all_samples(self, gallery_id: str) -> tuple[List[FaceSample], dict[str, str]]:
        with self._lock:
            gallery = self._load_gallery_unlocked(gallery_id)
            if gallery is None:
                raise ValueError(f"gallery not found: {gallery_id}")
            samples: List[FaceSample] = []
            names: dict[str, str] = {}
            for person in gallery.persons.values():
                names[person.person_id] = person.name
                samples.extend(person.samples)
            return samples, names

    def get_gallery_index(self, gallery_id: str) -> Any:
        """带 updated_at 失效的检索索引（numpy / faiss）。"""
        from app.small_models.face.gallery_index import GalleryIndex

        with self._lock:
            gallery = self._load_gallery_unlocked(gallery_id)
            if gallery is None:
                raise ValueError(f"gallery not found: {gallery_id}")

            cached = _index_cache.get(gallery_id)
            if cached is not None and cached[0] == gallery.updated_at:
                return cached[1]

            samples: List[FaceSample] = []
            names: dict[str, str] = {}
            for person in gallery.persons.values():
                names[person.person_id] = person.name
                samples.extend(person.samples)

            index = GalleryIndex.build(samples, names, gallery.updated_at)
            _index_cache[gallery_id] = (gallery.updated_at, index)
            return index

    def gallery_index_stats(self, gallery_id: str) -> dict[str, Any]:
        index = self.get_gallery_index(gallery_id)
        return {
            "gallery_id": gallery_id,
            "backend": index.backend,
            "sample_count": index.sample_count,
            "gallery_updated_at": index.gallery_updated_at,
        }

    def save_enroll_image(self, gallery_id: str, person_id: str, sample_id: str, image_bytes: bytes) -> str:
        img_dir = self._gallery_images_dir(gallery_id) / person_id
        img_dir.mkdir(parents=True, exist_ok=True)
        path = img_dir / f"{sample_id}.jpg"
        path.write_bytes(image_bytes)
        return str(path.resolve())


def get_face_gallery_store(base_dir: str | None = None) -> FaceGalleryStore:
    global _store_singleton
    with _store_lock:
        if _store_singleton is None:
            _store_singleton = FaceGalleryStore(base_dir=base_dir)
        return _store_singleton
