from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

from app.small_models.face.models import FaceSample


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
    """1:N 比对；内部委托 GalleryIndex（小规模 numpy，≥32 样本且已装 faiss 时加速）。"""
    from app.small_models.face.gallery_index import GalleryIndex

    if not samples:
        return None
    index = GalleryIndex.build(samples, person_names, gallery_updated_at="")
    return index.search(query_embedding, threshold=threshold)
