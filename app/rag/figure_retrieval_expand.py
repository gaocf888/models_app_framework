from __future__ import annotations

from typing import Any, Sequence

from app.core.config import get_app_config
from app.rag.models import RetrievedChunk
from app.rag.namespace_kb import chunk_passes_kb_enabled_filter
from app.rag.rag_service import RAGService
from app.rag.vector_store import VectorStore


def expand_related_figures(
    chunks: list[RetrievedChunk],
    store: VectorStore,
    *,
    namespace: str | None = None,
    max_per_text: int | None = None,
    max_total: int | None = None,
    pipeline_version: str | None = None,
) -> list[RetrievedChunk]:
    """
    正文 chunk 命中后，按 metadata.related_figure_ids 扩展关联 figure chunk（缓解 1）。
    """
    cfg = get_app_config().rag.ingestion
    if not cfg.figure_enabled:
        return chunks

    max_per_text = max_per_text if max_per_text is not None else cfg.figure_expand_max_per_text
    max_total = max_total if max_total is not None else cfg.figure_expand_max_total
    if max_total <= 0:
        return chunks

    existing_ids = {c.chunk_id for c in chunks if c.chunk_id}
    to_fetch: list[str] = []
    per_text_budget: dict[str, int] = {}

    for c in chunks:
        meta = c.metadata or {}
        if meta.get("content_type") == "figure":
            continue
        rel = meta.get("related_figure_ids") or []
        if not isinstance(rel, list):
            continue
        budget = max_per_text
        for fid in rel:
            if budget <= 0 or len(to_fetch) >= max_total:
                break
            sid = str(fid)
            if sid in existing_ids or sid in to_fetch:
                continue
            to_fetch.append(sid)
            existing_ids.add(sid)
            budget -= 1
            per_text_budget[c.chunk_id or ""] = budget

    if not to_fetch:
        return chunks

    hits = store.get_chunks_by_ext_ids(to_fetch[:max_total], namespace=namespace)
    hit_by_id = {str(h.get("ext_id")): h for h in hits if h.get("ext_id")}

    out: list[RetrievedChunk] = []
    expanded = 0
    for c in chunks:
        out.append(c)
        meta = c.metadata or {}
        if meta.get("content_type") == "figure":
            continue
        rel = meta.get("related_figure_ids") or []
        if not isinstance(rel, list):
            continue
        added_for_text = 0
        for fid in rel:
            if added_for_text >= max_per_text or expanded >= max_total:
                break
            sid = str(fid)
            h = hit_by_id.get(sid)
            if not h or not h.get("text"):
                continue
            fig_meta = h.get("metadata") if isinstance(h.get("metadata"), dict) else {}
            if not chunk_passes_kb_enabled_filter(fig_meta):
                continue
            if any(x.chunk_id == sid for x in out):
                continue
            base_score = float(c.score or 0.0)
            fig_chunk = RAGService._hit_to_chunk(  # noqa: SLF001
                {
                    **h,
                    "score": base_score * 0.95 if base_score else h.get("score"),
                },
                pipeline_version,
            )
            out.append(fig_chunk)
            added_for_text += 1
            expanded += 1

    return out
