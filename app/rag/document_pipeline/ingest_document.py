from __future__ import annotations

from typing import Any

from app.core.config import get_app_config
from app.rag.document_pipeline import ChunkingConfig, DocumentPipeline
from app.rag.document_pipeline.figure_extractor import (
    build_figure_chunks_from_extracted,
    extract_figures_from_document,
)
from app.rag.document_pipeline.figure_link import merge_and_link
from app.rag.document_pipeline.figure_pipeline import process_image_document
from app.rag.models import ChunkRecord, DocumentSource


def build_chunks_for_document(
    doc: DocumentSource,
    pipeline: DocumentPipeline | None = None,
) -> tuple[list[ChunkRecord], dict[str, Any], dict[str, Any]]:
    """
    统一文档切块（含 figure）。返回 (chunks, pipeline_stats, figure_metrics)。
    """
    ingest_cfg = get_app_config().rag.ingestion
    figure_metrics: dict[str, Any] = {}
    st = (doc.source_type or "text").lower()

    if st == "image":
        if not ingest_cfg.figure_enabled:
            raise ValueError("E_FIGURE_DISABLED: set RAG_FIGURE_ENABLED=true to ingest images")
        chunks, figure_metrics = process_image_document(doc)
        return chunks, {"chunk_count": len(chunks), "figure_only": True}, figure_metrics

    pipe = pipeline or DocumentPipeline(
        ChunkingConfig(
            chunk_size=ingest_cfg.chunk_size,
            chunk_overlap=ingest_cfg.chunk_overlap,
            min_chunk_size=ingest_cfg.min_chunk_size,
        )
    )
    staged = pipe.process_document_staged(doc)
    text_chunks = staged["chunks"]
    stats = dict(staged.get("stats") or {})

    if ingest_cfg.figure_enabled:
        parsed = staged.get("parsed") or ""
        extracted, neighbor_override = extract_figures_from_document(parsed=parsed, doc=doc, staged=staged)
        neighbor_parsed = neighbor_override if neighbor_override else parsed
        figure_chunks, figure_metrics = build_figure_chunks_from_extracted(
            doc=doc, parsed=neighbor_parsed, figures=extracted
        )
        if figure_chunks:
            all_chunks = merge_and_link(text_chunks, figure_chunks)
            stats["figure_count"] = figure_metrics.get("figure_count", len(figure_chunks))
            return all_chunks, stats, figure_metrics

    return text_chunks, stats, figure_metrics
