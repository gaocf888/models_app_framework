"""Milvus 人脸向量后端单元测试（mock pymilvus，无需真实 Milvus 服务）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from app.small_models.strategy.face.backends.milvus_index import (
    MilvusGalleryIndex,
    milvus_upsert_sample,
)
from app.small_models.strategy.face.gallery_index import build_gallery_index
from app.small_models.strategy.face.gallery_store import FaceGalleryStore
from app.small_models.strategy.face.models import FaceSample


def test_build_gallery_index_milvus_backend() -> None:
    with patch("app.small_models.strategy.face.gallery_index.use_milvus_backend", return_value=True):
        idx = build_gallery_index("g1", [], {}, "t1")
    assert isinstance(idx, MilvusGalleryIndex)
    assert idx.backend == "milvus"
    assert idx.gallery_id == "g1"


def test_milvus_gallery_index_search() -> None:
    hit_entity = MagicMock()
    hit_entity.get.side_effect = lambda k: {
        "person_id": "p1",
        "person_name": "Alice",
        "sample_id": "s1",
    }.get(k)

    hit = MagicMock()
    hit.score = 0.92
    hit.entity = hit_entity

    mock_col = MagicMock()
    mock_col.search.return_value = [[hit]]

    idx = MilvusGalleryIndex(gallery_id="g1", gallery_updated_at="t1", sample_count=1)
    with patch(
        "app.small_models.strategy.face.backends.milvus_index.get_face_milvus_collection",
        return_value=mock_col,
    ):
        result = idx.search([1.0, 0.0, 0.0], threshold=0.5)

    assert result is not None
    assert result.person_id == "p1"
    assert result.person_name == "Alice"
    assert result.score == 0.92


def test_milvus_gallery_index_search_below_threshold() -> None:
    hit = MagicMock()
    hit.score = 0.3
    hit.entity = MagicMock()

    mock_col = MagicMock()
    mock_col.search.return_value = [[hit]]

    idx = MilvusGalleryIndex(gallery_id="g1", gallery_updated_at="t1", sample_count=1)
    with patch(
        "app.small_models.strategy.face.backends.milvus_index.get_face_milvus_collection",
        return_value=mock_col,
    ):
        result = idx.search([1.0, 0.0, 0.0], threshold=0.5)
    assert result is None


def test_store_enroll_syncs_milvus(tmp_path) -> None:
    store = FaceGalleryStore(base_dir=str(tmp_path / "galleries"))
    store.create_gallery("g1")

    mock_col = MagicMock()
    with patch("app.small_models.strategy.face.gallery_store.use_milvus_backend", return_value=True):
        with patch(
            "app.small_models.strategy.face.backends.milvus_index.get_face_milvus_collection",
            return_value=mock_col,
        ):
            store.enroll("g1", person_id="p1", name="Bob", embedding=[1.0, 0.0, 0.0])

    mock_col.upsert.assert_called_once()
    mock_col.flush.assert_called()


def test_milvus_upsert_normalizes_embedding() -> None:
    sample = FaceSample(sample_id="s1", person_id="p1", embedding=[3.0, 4.0])
    mock_col = MagicMock()
    with patch(
        "app.small_models.strategy.face.backends.milvus_index.get_face_milvus_collection",
        return_value=mock_col,
    ):
        milvus_upsert_sample("g1", sample, person_name="Bob")

    args = mock_col.upsert.call_args[0][0]
    emb = np.asarray(args[4][0], dtype=np.float32)
    norm = float(np.linalg.norm(emb))
    assert abs(norm - 1.0) < 1e-5
