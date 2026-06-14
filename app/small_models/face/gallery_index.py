from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

from app.core.logging import get_logger
from app.small_models.face.matcher import FaceMatchResult, _normalize_rows
from app.small_models.face.models import FaceSample

logger = get_logger(__name__)

# 样本数达到该阈值且已安装 faiss 时启用 IndexFlatIP
FAISS_MIN_SAMPLES = 32


@dataclass
class GalleryIndex:
    """人脸库检索索引；小规模 numpy，大规模可选 Faiss。"""

    samples: List[FaceSample]
    person_names: dict[str, str]
    gallery_updated_at: str
    backend: str  # "numpy" | "faiss"
    sample_count: int

    _matrix: np.ndarray | None = None
    _faiss_index: object | None = None

    @classmethod
    def build(
        cls,
        samples: List[FaceSample],
        person_names: dict[str, str],
        gallery_updated_at: str,
    ) -> GalleryIndex:
        idx = cls(
            samples=list(samples),
            person_names=dict(person_names),
            gallery_updated_at=gallery_updated_at,
            backend="numpy",
            sample_count=len(samples),
        )
        if not samples:
            return idx

        mat = np.asarray([s.embedding for s in samples], dtype=np.float32)
        idx._matrix = _normalize_rows(mat)

        if len(samples) >= FAISS_MIN_SAMPLES:
            try:
                import faiss  # type: ignore[import-not-found]

                dim = idx._matrix.shape[1]
                faiss_index = faiss.IndexFlatIP(dim)
                faiss_index.add(idx._matrix)
                idx._faiss_index = faiss_index
                idx.backend = "faiss"
                logger.debug("gallery index using faiss: samples=%d dim=%d", len(samples), dim)
            except ImportError:
                logger.debug("faiss not installed, gallery index uses numpy: samples=%d", len(samples))

        return idx

    def search(self, query_embedding: Sequence[float], *, threshold: float) -> FaceMatchResult | None:
        if not self.samples or self._matrix is None:
            return None

        q = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
        q = _normalize_rows(q)

        if self._faiss_index is not None:
            scores, indices = self._faiss_index.search(q, 1)  # type: ignore[union-attr]
            best_idx = int(indices[0][0])
            best_score = float(scores[0][0])
        else:
            scores = (self._matrix @ q.T).reshape(-1)
            best_idx = int(np.argmax(scores))
            best_score = float(scores[best_idx])

        if best_score < threshold:
            return None

        sample = self.samples[best_idx]
        return FaceMatchResult(
            person_id=sample.person_id,
            person_name=self.person_names.get(sample.person_id, sample.person_id),
            sample_id=sample.sample_id,
            score=best_score,
            match_type="identified",
        )
