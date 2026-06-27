from __future__ import annotations

from typing import List, Protocol, Sequence, runtime_checkable

from app.small_models.strategy.face.backends.local_index import LocalGalleryIndex
from app.small_models.strategy.face.matcher import FaceMatchResult
from app.small_models.strategy.face.models import FaceSample
from app.small_models.strategy.face.vector_config import use_milvus_backend

# 向后兼容：历史代码与测试中的 GalleryIndex 即 LocalGalleryIndex
GalleryIndex = LocalGalleryIndex


@runtime_checkable
class FaceGalleryIndex(Protocol):
    backend: str
    sample_count: int
    gallery_updated_at: str

    def search(self, query_embedding: Sequence[float], *, threshold: float) -> FaceMatchResult | None: ...


def build_gallery_index(
    gallery_id: str,
    samples: List[FaceSample],
    person_names: dict[str, str],
    gallery_updated_at: str,
) -> FaceGalleryIndex:
    if use_milvus_backend():
        from app.small_models.strategy.face.backends.milvus_index import MilvusGalleryIndex

        return MilvusGalleryIndex(
            gallery_id=gallery_id,
            gallery_updated_at=gallery_updated_at,
            sample_count=len(samples),
        )
    return LocalGalleryIndex.build(samples, person_names, gallery_updated_at)
