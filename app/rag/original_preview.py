"""
原文预览 URL：稳定引用（minio:// / local: / object key）→ 短时可打开的 HTTPS/本地路径。

方案 A：
- ``original_content_url``：稳定对象引用（可落库）；
- ``original_preview_url``：预签名或可直接打开的 URL（仅响应下发，落库前剥离）。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.core.logging import get_logger

logger = get_logger(__name__)

PREVIEW_URL_FIELD = "original_preview_url"
STABLE_URL_FIELD = "original_content_url"


def _is_http_url(value: str) -> bool:
    low = (value or "").strip().lower()
    return low.startswith("http://") or low.startswith("https://")


def resolve_original_preview_url(ref: str | None) -> str | None:
    """
    将稳定引用解析为前端可打开的预览 URL。

    - 已是 http(s)：原样返回；
    - minio:// / rag-docs/ object key / local:：签发预签名或本地可访问路径；
    - 失败：返回 None（不抛，避免拖垮 citations）。
    """
    raw = (ref or "").strip()
    if not raw:
        return None
    if _is_http_url(raw):
        return raw
    try:
        from app.rag.asset_storage import RagAssetStorage
        from app.rag.original_docs import looks_like_object_ref

        if not looks_like_object_ref(raw) and not raw.startswith("rag-docs/"):
            return None
        return RagAssetStorage().presign_original_ref(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve original preview failed: ref=%s err=%s", raw[:160], exc)
        return None


def enrich_citation_with_preview(item: dict[str, Any]) -> dict[str, Any]:
    """就地/拷贝写入 ``original_preview_url``（有稳定 URL 时）。"""
    if not isinstance(item, dict):
        return item
    out = dict(item)
    stable = out.get(STABLE_URL_FIELD)
    if not isinstance(stable, str) or not stable.strip():
        out.pop(PREVIEW_URL_FIELD, None)
        return out
    preview = resolve_original_preview_url(stable.strip())
    if preview:
        out[PREVIEW_URL_FIELD] = preview
    else:
        out.pop(PREVIEW_URL_FIELD, None)
    return out


def enrich_rag_citations_with_preview(
    citations: list[Any] | None,
) -> list[dict[str, Any]]:
    """为 citation 列表补齐短时 ``original_preview_url``。"""
    if not citations:
        return []
    out: list[dict[str, Any]] = []
    for it in citations:
        if isinstance(it, dict):
            out.append(enrich_citation_with_preview(it))
    return out


def strip_rag_citation_preview_urls(
    citations: list[Any] | None,
) -> list[dict[str, Any]] | None:
    """
    落库前去掉 ``original_preview_url``（保留 ``original_content_url`` 稳定引用）。
    返回 None 表示调用方未传 citations。
    """
    if citations is None:
        return None
    out: list[dict[str, Any]] = []
    for it in citations:
        if not isinstance(it, dict):
            continue
        row = dict(it)
        row.pop(PREVIEW_URL_FIELD, None)
        out.append(row)
    return out


def looks_like_stable_original_ref(value: str | None) -> bool:
    """是否可作为原文 resolve/presign 的入参。"""
    raw = (value or "").strip()
    if not raw:
        return False
    if _is_http_url(raw):
        return True
    from app.rag.original_docs import looks_like_object_ref

    if looks_like_object_ref(raw):
        return True
    # 裸 object key（常见于历史/运维传参）
    parsed = urlparse(raw)
    if parsed.scheme:
        return False
    return raw.startswith("rag-docs/") or "/" in raw
