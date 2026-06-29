"""阶段 2：figure 召回扩展（缓解 1）单元测试。"""

from __future__ import annotations

from unittest.mock import patch

from app.rag.figure_retrieval_expand import expand_related_figures
from app.rag.models import RetrievedChunk
from app.rag.vector_store import InMemoryVectorStore


def test_expand_related_figures_pulls_figure_with_image_url():
    store = InMemoryVectorStore()
    store.add_texts(
        ["figure body with diagram"],
        embeddings=[[1.0, 0.0]],
        ids=["fig-1"],
        namespace="ns",
        doc_name="doc",
        metadatas=[{"content_type": "figure", "image_url": "http://img/1.png", "chunk_id": "fig-1"}],
    )
    text_chunk = RetrievedChunk(
        text="正文关于膨胀节",
        doc_name="doc",
        namespace="ns",
        chunk_id="text-1",
        metadata={"related_figure_ids": ["fig-1"]},
        score=0.9,
    )
    with patch("app.rag.figure_retrieval_expand.get_app_config") as mock_cfg:
        mock_cfg.return_value.rag.ingestion.figure_enabled = True
        mock_cfg.return_value.rag.ingestion.figure_expand_max_per_text = 2
        mock_cfg.return_value.rag.ingestion.figure_expand_max_total = 6
        out = expand_related_figures([text_chunk], store, namespace="ns", pipeline_version="1.0.0")
    assert len(out) == 2
    assert out[1].metadata.get("image_url") == "http://img/1.png"
    assert out[1].metadata.get("content_type") == "figure"
