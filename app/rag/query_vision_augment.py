from __future__ import annotations

from typing import Sequence

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.models import RetrievedChunk
from app.rag.vision_caption_service import VisionCaptionService

logger = get_logger(__name__)


def augment_query_with_image(text_query: str, image_url: str) -> str:
    """用 VLM 描述用户附图，拼接到文本 query 后供向量/关键词检索。"""
    base = (text_query or "").strip()
    url = (image_url or "").strip()
    if not url:
        return base
    try:
        caption = VisionCaptionService().caption_figure(url, context=base or None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("query vision augment caption failed: %s", exc)
        return base
    caption = (caption or "").strip()
    if not caption:
        return base
    if base:
        return f"{base}\n\n[用户附图描述]\n{caption}"
    return f"[用户附图描述]\n{caption}"


def merge_retrieved_chunks(
    primary: Sequence[RetrievedChunk],
    secondary: Sequence[RetrievedChunk],
    *,
    max_k: int,
) -> list[RetrievedChunk]:
    """hybrid 模式：按 chunk_id 去重，保留较高 score。"""
    merged: dict[str, RetrievedChunk] = {}
    for c in list(primary) + list(secondary):
        key = str(c.chunk_id or c.text[:120])
        prev = merged.get(key)
        if prev is None or float(c.score or 0.0) > float(prev.score or 0.0):
            merged[key] = c
    out = sorted(merged.values(), key=lambda x: float(x.score or 0.0), reverse=True)
    return out[: max(1, max_k)]


def resolve_retrieval_query(
    text_query: str,
    query_image_url: str | None,
) -> tuple[str, str | None]:
    """
    返回 (effective_query, augmented_query_for_hybrid)。
    - 未启用或无附图：原 query，第二项为 None。
    - vision_augmented：effective 为增强句，第二项 None。
    - hybrid：effective 为原 query，第二项为增强句。
    """
    cfg = get_app_config().rag.query_vision
    url = (query_image_url or "").strip()
    if not cfg.enabled or not url:
        return text_query, None
    mode = (cfg.mode or "vision_augmented").strip().lower()
    aug = augment_query_with_image(text_query, url)
    if mode == "hybrid":
        return text_query, aug
    return aug, None
