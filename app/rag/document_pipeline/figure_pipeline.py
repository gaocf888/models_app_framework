from __future__ import annotations

import time
from typing import Any

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.asset_storage import RagAssetStorage
from app.rag.document_pipeline.enrichers import chunk_hash, make_chunk_meta
from app.rag.document_pipeline.figure_text import format_figure_chunk_text
from app.rag.models import ChunkRecord, DocumentSource
from app.rag.vision_caption_service import VisionCaptionService

logger = get_logger(__name__)


def _neighbor_context(source: DocumentSource) -> str | None:
    """VLM 上下文：仅 manual_context，不含 description（description 单独走跳过 VLM）。"""
    meta = source.metadata or {}
    for key in ("manual_context",):
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _manual_caption_text(source: DocumentSource) -> str | None:
    """
    §4.5：manual_caption 或 description 存在时跳过 VLM，直接作为 figure 描述文本。
    """
    meta = source.metadata or {}
    manual = meta.get("manual_caption")
    if isinstance(manual, str) and manual.strip():
        return manual.strip()
    if source.description and source.description.strip():
        return source.description.strip()
    return None


def _build_figure_chunk(
    *,
    source: DocumentSource,
    text: str,
    figure_index: int,
    image_url: str,
    image_object_key: str,
    caption_source: str,
    extra_meta: dict[str, Any] | None = None,
) -> ChunkRecord:
    meta = make_chunk_meta(
        doc_name=source.doc_name,
        chunk_index=figure_index,
        namespace=source.namespace,
        source_uri=source.source_uri,
    )
    meta.update(
        {
            "content_type": "figure",
            "image_url": image_url,
            "image_object_key": image_object_key,
            "figure_index": figure_index,
            "parent_doc_name": source.doc_name,
            "caption_source": caption_source,
            "asset_type": (source.metadata or {}).get("asset_type"),
        }
    )
    if extra_meta:
        meta.update(extra_meta)
    meta["chunk_hash"] = chunk_hash(text)
    return ChunkRecord(chunk_id=meta["chunk_id"], chunk_index=figure_index, text=text, metadata=meta)


def process_image_document(source: DocumentSource) -> tuple[list[ChunkRecord], dict[str, Any]]:
    """
    独立图片文档（source_type=image）→ 单 figure chunk。
    返回 (chunks, metrics)。
    """
    storage = RagAssetStorage()
    metrics: dict[str, Any] = {"figure_count": 0, "vlm_caption_ms": 0}

    asset = storage.ensure_image_url(
        content=source.content,
        doc_name=source.doc_name,
        doc_version=source.doc_version,
        figure_index=0,
    )
    image_url = asset["image_url"]
    image_key = asset.get("image_object_key") or image_url

    skip_caption = _manual_caption_text(source)
    neighbor = _neighbor_context(source)
    caption_source = "vlm"
    caption = ""

    if skip_caption:
        caption = skip_caption
        caption_source = "manual"
    else:
        t0 = time.perf_counter()
        try:
            caption = VisionCaptionService().caption_figure(image_url, context=neighbor)
        except Exception:
            caption = neighbor or f"【图块】文档《{source.doc_name}》（VLM 描述失败，请查看附图）"
            caption_source = "failed"
            metrics["caption_failed_count"] = 1
        metrics["vlm_caption_ms"] = int((time.perf_counter() - t0) * 1000)

    if not caption.strip():
        caption = f"【图块】文档《{source.doc_name}》"

    neighbor_before: str | None = None
    if caption_source == "manual":
        if neighbor and neighbor != caption:
            neighbor_before = neighbor
    else:
        neighbor_before = neighbor

    body = format_figure_chunk_text(
        caption=caption,
        neighbor_before=neighbor_before,
        doc_name=source.doc_name,
        section_label=(source.metadata or {}).get("asset_type"),
    )

    chunk = _build_figure_chunk(
        source=source,
        text=body,
        figure_index=0,
        image_url=image_url,
        image_object_key=image_key,
        caption_source=caption_source,
    )
    metrics["figure_count"] = 1
    return [chunk], metrics
