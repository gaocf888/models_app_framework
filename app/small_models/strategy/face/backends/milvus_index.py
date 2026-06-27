from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

from app.core.logging import get_logger
from app.small_models.strategy.face.backends.milvus_client import (
    get_face_milvus_collection,
    milvus_filter_and,
    milvus_filter_eq,
)
from app.small_models.strategy.face.matcher import FaceMatchResult, _normalize_rows
from app.small_models.strategy.face.models import FaceSample
from app.small_models.strategy.face.vector_config import get_face_vector_config

logger = get_logger(__name__)


@dataclass
class MilvusGalleryIndex:
    """Milvus 1:N 检索；向量在 Milvus，元数据仍由 JSON 维护。"""

    gallery_id: str
    gallery_updated_at: str
    sample_count: int
    backend: str = "milvus"

    def search(self, query_embedding: Sequence[float], *, threshold: float) -> FaceMatchResult | None:
        if self.sample_count <= 0:
            return None

        cfg = get_face_vector_config()
        collection = get_face_milvus_collection()
        q = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
        q = _normalize_rows(q)

        expr = milvus_filter_eq("gallery_id", self.gallery_id)
        results = collection.search(
            data=q.tolist(),
            anns_field="embedding",
            param={"metric_type": cfg.milvus_metric, "params": {}},
            limit=1,
            expr=expr,
            output_fields=["person_id", "person_name", "sample_id"],
        )
        if not results or not results[0]:
            return None

        hit = results[0][0]
        best_score = float(hit.score)
        if best_score < threshold:
            return None

        entity = hit.entity
        person_id = str(entity.get("person_id"))
        return FaceMatchResult(
            person_id=person_id,
            person_name=str(entity.get("person_name") or person_id),
            sample_id=str(entity.get("sample_id")),
            score=best_score,
            match_type="identified",
        )


def milvus_upsert_sample(
    gallery_id: str,
    sample: FaceSample,
    *,
    person_name: str,
) -> None:
    collection = get_face_milvus_collection()
    embedding = _normalize_rows(
        np.asarray([sample.embedding], dtype=np.float32)
    )[0].tolist()
    collection.upsert(
        [
            [sample.sample_id],
            [gallery_id],
            [sample.person_id],
            [person_name or sample.person_id],
            [embedding],
        ]
    )
    collection.flush()
    logger.debug("milvus upsert sample: gallery=%s sample=%s", gallery_id, sample.sample_id)


def milvus_delete_samples(gallery_id: str, *, person_id: str | None = None) -> None:
    collection = get_face_milvus_collection()
    parts = [milvus_filter_eq("gallery_id", gallery_id)]
    if person_id is not None:
        parts.append(milvus_filter_eq("person_id", person_id))
    expr = milvus_filter_and(*parts)
    collection.delete(expr)
    collection.flush()
    logger.debug("milvus delete: gallery=%s person=%s", gallery_id, person_id)


def milvus_count_samples(gallery_id: str) -> int:
    collection = get_face_milvus_collection()
    expr = milvus_filter_eq("gallery_id", gallery_id)
    rows = collection.query(expr=expr, output_fields=["sample_id"])
    return len(rows)


def milvus_sync_gallery(
    gallery_id: str,
    samples: List[FaceSample],
    person_names: dict[str, str],
) -> None:
    """全量同步某库（迁移或修复时用）。"""
    milvus_delete_samples(gallery_id)
    for sample in samples:
        milvus_upsert_sample(
            gallery_id,
            sample,
            person_name=person_names.get(sample.person_id, sample.person_id),
        )
