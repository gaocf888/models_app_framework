from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class FaceSample:
    sample_id: str
    person_id: str
    embedding: List[float]
    source_image: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "person_id": self.person_id,
            "embedding": self.embedding,
            "source_image": self.source_image,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FaceSample:
        return cls(
            sample_id=str(data["sample_id"]),
            person_id=str(data["person_id"]),
            embedding=[float(x) for x in data["embedding"]],
            source_image=data.get("source_image"),
            created_at=str(data.get("created_at") or _utc_now_iso()),
        )


@dataclass
class FacePerson:
    person_id: str
    name: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    samples: List[FaceSample] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "person_id": self.person_id,
            "name": self.name,
            "metadata": dict(self.metadata),
            "samples": [s.to_dict() for s in self.samples],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FacePerson:
        return cls(
            person_id=str(data["person_id"]),
            name=str(data.get("name") or data["person_id"]),
            metadata=dict(data.get("metadata") or {}),
            samples=[FaceSample.from_dict(s) for s in (data.get("samples") or [])],
        )


@dataclass
class FaceGallery:
    gallery_id: str
    name: str
    model_pack: str = "buffalo_l"
    persons: Dict[str, FacePerson] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def touch(self) -> None:
        self.updated_at = _utc_now_iso()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gallery_id": self.gallery_id,
            "name": self.name,
            "model_pack": self.model_pack,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "persons": {pid: p.to_dict() for pid, p in self.persons.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FaceGallery:
        persons_raw = data.get("persons") or {}
        return cls(
            gallery_id=str(data["gallery_id"]),
            name=str(data.get("name") or data["gallery_id"]),
            model_pack=str(data.get("model_pack") or "buffalo_l"),
            persons={str(k): FacePerson.from_dict(v) for k, v in persons_raw.items()},
            created_at=str(data.get("created_at") or _utc_now_iso()),
            updated_at=str(data.get("updated_at") or _utc_now_iso()),
        )


def new_sample_id() -> str:
    return uuid4().hex
