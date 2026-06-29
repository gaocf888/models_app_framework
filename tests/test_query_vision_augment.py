from __future__ import annotations

from unittest.mock import patch

from app.rag.models import RetrievedChunk
from app.rag.query_vision_augment import augment_query_with_image, merge_retrieved_chunks, resolve_retrieval_query


def test_resolve_retrieval_query_disabled_returns_original():
    with patch("app.rag.query_vision_augment.get_app_config") as mock_cfg:
        mock_cfg.return_value.rag.query_vision.enabled = False
        q, aug = resolve_retrieval_query("hello", "http://img/x.png")
    assert q == "hello"
    assert aug is None


def test_resolve_retrieval_query_hybrid_mode():
    with patch("app.rag.query_vision_augment.get_app_config") as mock_cfg:
        mock_cfg.return_value.rag.query_vision.enabled = True
        mock_cfg.return_value.rag.query_vision.mode = "hybrid"
        with patch("app.rag.query_vision_augment.augment_query_with_image", return_value="hello\n\n[用户附图描述]\ncap"):
            q, aug = resolve_retrieval_query("hello", "http://img/x.png")
    assert q == "hello"
    assert aug == "hello\n\n[用户附图描述]\ncap"


def test_merge_retrieved_chunks_dedupes_by_chunk_id():
    a = RetrievedChunk(text="a", chunk_id="1", score=0.5)
    b = RetrievedChunk(text="b", chunk_id="2", score=0.9)
    c = RetrievedChunk(text="a2", chunk_id="1", score=0.8)
    out = merge_retrieved_chunks([a, b], [c], max_k=5)
    assert len(out) == 2
    by_id = {x.chunk_id: x for x in out}
    assert by_id["1"].score == 0.8


def test_augment_query_with_image_on_vlm_failure_returns_original():
    with patch("app.rag.query_vision_augment.VisionCaptionService") as mock_svc:
        mock_svc.return_value.caption_figure.side_effect = RuntimeError("vlm down")
        assert augment_query_with_image("q", "http://x") == "q"
