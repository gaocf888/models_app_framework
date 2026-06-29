from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.llm.graphs.chatbot_rag_citations import chunks_to_rag_context
from app.rag.document_pipeline.figure_link import merge_and_link
from app.rag.document_pipeline.figure_text import format_figure_chunk_text, slice_neighbor_text
from app.rag.figure_retrieval_expand import expand_related_figures
from app.rag.models import ChunkRecord, RetrievedChunk
from app.rag.vector_store import InMemoryVectorStore


def test_format_figure_chunk_text_includes_neighbor_and_caption():
    text = format_figure_chunk_text(
        neighbor_before="膨胀节布置如下图所示",
        caption="图中标注 A/B 管排",
        doc_name="手册",
    )
    assert "【邻近正文-前】" in text
    assert "膨胀节" in text
    assert "A/B" in text


def test_slice_neighbor_text_respects_budget():
    full = "膨胀节布置如下图所示，应注意预留补偿量。[FIG:0]安装完成后需通球试验。"
    anchor = full.index("[FIG:0]")
    before, after = slice_neighbor_text(
        full,
        anchor_start=anchor,
        anchor_end=anchor + len("[FIG:0]"),
        max_chars=40,
        before_ratio=0.7,
    )
    assert "膨胀节" in before
    assert "通球" in after


def test_merge_and_link_sets_bidirectional_ids():
    t0 = ChunkRecord(chunk_id="t0", chunk_index=0, text="正文", metadata={"chunk_id": "t0"})
    f0 = ChunkRecord(
        chunk_id="f0",
        chunk_index=100000,
        text="图描述",
        metadata={"chunk_id": "f0", "content_type": "figure", "figure_index": 0},
    )
    merged = merge_and_link([t0], [f0])
    assert t0.metadata["related_figure_ids"] == ["f0"]
    assert f0.metadata["parent_chunk_id"] == "t0"
    assert len(merged) == 2


def test_expand_related_figures_pulls_figure_chunk():
    store = InMemoryVectorStore()
    store.add_texts(
        ["figure body"],
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


def test_citations_include_image_url_for_figure():
    chunks = [
        RetrievedChunk(
            text="【图块】描述",
            doc_name="d",
            chunk_id="c1",
            metadata={"content_type": "figure", "image_url": "http://x/y.png", "asset_type": "diagram"},
        )
    ]
    _, citations = chunks_to_rag_context(chunks)
    assert citations[0]["content_type"] == "figure"
    assert citations[0]["image_url"] == "http://x/y.png"
    assert citations[0]["asset_type"] == "diagram"


@patch("app.rag.document_pipeline.figure_pipeline.RagAssetStorage")
@patch("app.rag.document_pipeline.figure_pipeline.VisionCaptionService")
def test_process_image_document_manual_caption(mock_vlm, mock_storage):
    mock_storage.return_value.ensure_image_url.return_value = {
        "image_url": "http://img/x.png",
        "image_object_key": "k",
    }
    from app.rag.document_pipeline.figure_pipeline import process_image_document
    from app.rag.models import DocumentSource

    doc = DocumentSource(
        dataset_id="ds",
        doc_name="fig1",
        namespace="ns",
        content="/tmp/x.png",
        source_type="image",
        metadata={"manual_caption": "人工描述架构图"},
    )
    chunks, metrics = process_image_document(doc)
    mock_vlm.return_value.caption_figure.assert_not_called()
    assert chunks[0].metadata["content_type"] == "figure"
    assert chunks[0].metadata["caption_source"] == "manual"
    assert "人工描述" in chunks[0].text
    assert metrics["figure_count"] == 1


@patch("app.rag.document_pipeline.figure_pipeline.RagAssetStorage")
@patch("app.rag.document_pipeline.figure_pipeline.VisionCaptionService")
def test_process_image_document_description_skips_vlm(mock_vlm, mock_storage):
    mock_storage.return_value.ensure_image_url.return_value = {
        "image_url": "http://img/x.png",
        "image_object_key": "k",
    }
    from app.rag.document_pipeline.figure_pipeline import process_image_document
    from app.rag.models import DocumentSource

    doc = DocumentSource(
        dataset_id="ds",
        doc_name="fig2",
        namespace="ns",
        content="/tmp/x.png",
        source_type="image",
        description="锅炉三级过热器架构说明",
    )
    chunks, _ = process_image_document(doc)
    mock_vlm.return_value.caption_figure.assert_not_called()
    assert chunks[0].metadata["caption_source"] == "manual"
    assert "锅炉三级过热器" in chunks[0].text


def test_merge_and_link_prefers_same_section_parent():
    t_sec1 = ChunkRecord(
        chunk_id="t1",
        chunk_index=0,
        text="## 第一章\n旧节正文",
        metadata={"chunk_id": "t1", "section_path": "第一章"},
    )
    t_sec2 = ChunkRecord(
        chunk_id="t2",
        chunk_index=1,
        text="## 第二章\n膨胀节布置如下图所示",
        metadata={"chunk_id": "t2", "section_path": "第二章"},
    )
    f0 = ChunkRecord(
        chunk_id="f0",
        chunk_index=100000,
        text="图描述",
        metadata={
            "chunk_id": "f0",
            "content_type": "figure",
            "parent_section_path": "第二章",
        },
    )
    merge_and_link([t_sec1, t_sec2], [f0])
    assert f0.metadata["parent_chunk_id"] == "t2"
    assert t_sec2.metadata["related_figure_ids"] == ["f0"]


def test_section_path_before_markdown():
    from app.rag.document_pipeline.figure_extractor import _section_path_before_markdown

    parsed = "## 总体架构\n\n前文\n\n![](images/a.png)\n"
    idx = parsed.find("images/a.png")
    assert idx > 0
    assert _section_path_before_markdown(parsed, idx) == "总体架构"


def test_build_chunks_for_document_rejects_image_when_disabled():
    from app.rag.document_pipeline.ingest_document import build_chunks_for_document
    from app.rag.models import DocumentSource

    doc = DocumentSource(
        dataset_id="ds",
        doc_name="x",
        namespace="ns",
        content="/tmp/x.png",
        source_type="image",
    )
    with patch("app.rag.document_pipeline.ingest_document.get_app_config") as mock_cfg:
        mock_cfg.return_value.rag.ingestion.figure_enabled = False
        with pytest.raises(ValueError, match="E_FIGURE_DISABLED"):
            build_chunks_for_document(doc)
