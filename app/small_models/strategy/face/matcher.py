from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

from app.small_models.strategy.face.models import FaceSample


@dataclass(frozen=True)
class FaceMatchResult:
    person_id: str
    person_name: str
    sample_id: str
    score: float
    match_type: str = "identified"


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return matrix / norms


def match_embedding_1n(
    query_embedding: Sequence[float],
    samples: List[FaceSample],
    *,
    person_names: dict[str, str],
    threshold: float,
) -> FaceMatchResult | None:
    """1:N 比对；内存样本列表始终走 local numpy/faiss（与向量库后端无关）。"""
    from app.small_models.strategy.face.backends.local_index import LocalGalleryIndex

    if not samples:
        return None
    index = LocalGalleryIndex.build(samples, person_names, gallery_updated_at="")
    return index.search(query_embedding, threshold=threshold)
