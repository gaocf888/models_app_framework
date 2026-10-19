"""
原文预览 URL：稳定引用（minio:// / local: / object key）→ 前端可打开的预览地址。

方案 A（修订）：
- ``original_content_url``：稳定对象引用（可落库）；
- ``original_preview_url``：经算法服务代理的预览路径（``/chatbot/documents/original/preview?ref=...``），
  避免返回集群内 ``models-app-minio:9000`` 预签名（外网浏览器不可达）。
  仅响应下发，落库前剥离。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from app.core.logging import get_logger

logger = get_logger(__name__)

PREVIEW_URL_FIELD = "original_preview_url"
STABLE_URL_FIELD = "original_content_url"
PROXY_PREVIEW_PATH = "/chatbot/documents/original/preview"


def _is_http_url(value: str) -> bool:
    low = (value or "").strip().lower()
    return low.startswith("http://") or low.startswith("https://")


def build_original_proxy_preview_url(ref: str) -> str:
    """构造经算法服务代理的原文预览相对路径（浏览器走 API/BFF，不直连 MinIO）。"""
    raw = (ref or "").strip()
    return f"{PROXY_PREVIEW_PATH}?ref={quote(raw, safe='')}"


def _configured_minio_endpoint_host() -> str:
    try:
        from app.core.config import get_app_config

        endpoint = (get_app_config().chatbot.image_minio_endpoint or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    # 允许 host:port 或带 scheme
    if "://" in endpoint:
        return (urlparse(endpoint).netloc or "").strip()
    return endpoint.split("/")[0].strip()


def _is_internal_minio_http(url: str) -> bool:
    host = _configured_minio_endpoint_host()
    if not host:
        return "models-app-minio" in (url or "")
    return host in (url or "")


def stable_ref_from_minio_http_url(url: str) -> str | None:
    """
    将 ``http(s)://models-app-minio:9000/{bucket}/{key}?X-Amz-...`` 还原为 ``minio://bucket/key``。
    """
    raw = (url or "").strip()
    if not _is_http_url(raw):
        return None
    parsed = urlparse(raw)
    path = unquote(parsed.path or "").lstrip("/")
    if not path or "/" not in path:
        return None
    bucket, key = path.split("/", 1)
    bucket = bucket.strip()
    key = key.strip()
    if not bucket or not key:
        return None
    return f"minio://{bucket}/{key}"


def resolve_original_preview_url(ref: str | None) -> str | None:
    """
    将稳定引用解析为前端可打开的预览 URL。

    - 公网 http(s)：原样返回；
    - 集群内 MinIO http(s) 预签名：还原为 minio:// 后走代理路径；
    - minio:// / rag-docs/ / local:：返回 ``/chatbot/documents/original/preview?ref=...``；
    - 失败：返回 None（不抛，避免拖垮 citations）。
    """
    raw = (ref or "").strip()
    if not raw:
        return None
    if _is_http_url(raw):
        if _is_internal_minio_http(raw):
            stable = stable_ref_from_minio_http_url(raw)
            if stable:
                return build_original_proxy_preview_url(stable)
        return raw
    try:
        from app.rag.original_docs import looks_like_object_ref

        if not looks_like_object_ref(raw) and not raw.startswith("rag-docs/"):
            return None
        return build_original_proxy_preview_url(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("resolve original preview failed: ref=%s err=%s", raw[:160], exc)
        return None


def guess_original_filename(ref: str) -> str:
    raw = (ref or "").strip()
    if _is_http_url(raw):
        path = unquote(urlparse(raw).path or "")
        name = Path(path).name
        return name or "original.bin"
    if raw.startswith("minio://"):
        path = unquote(urlparse(raw).path or "")
        name = Path(path).name
        return name or "original.bin"
    if raw.startswith("local:"):
        return Path(raw[len("local:") :].lstrip("/")).name or "original.bin"
    return Path(raw).name or "original.bin"


def guess_original_media_type(filename: str) -> str:
    ctype, _ = mimetypes.guess_type(filename or "")
    return ctype or "application/octet-stream"


def load_original_bytes_for_preview(ref: str) -> tuple[bytes, str, str]:
    """
    读取原文字节供代理预览。

    :return: (data, media_type, filename)
    """
    from app.rag.asset_storage import RagAssetStorage
    from app.rag.original_docs import OriginalObjectError

    raw = (ref or "").strip()
    if not raw:
        raise ValueError("ref is required")
    if _is_http_url(raw) and _is_internal_minio_http(raw):
        converted = stable_ref_from_minio_http_url(raw)
        if converted:
            raw = converted
    if not looks_like_stable_original_ref(raw):
        raise ValueError("invalid original ref")
    # 公网 http(s) 不应走本代理读盘；调用方应直接打开
    if _is_http_url(raw) and not _is_internal_minio_http(raw):
        raise ValueError("public http(s) url should be opened directly, not proxied")
    try:
        data, _ = RagAssetStorage().get_original_bytes(raw)
    except OriginalObjectError as exc:
        raise FileNotFoundError(str(exc)) from exc
    filename = guess_original_filename(raw)
    return data, guess_original_media_type(filename), filename


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
    """为 citation 列表补齐 ``original_preview_url``（代理路径或公网 http）。"""
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
    """是否可作为原文 resolve/proxy 的入参。"""
    raw = (value or "").strip()
    if not raw:
        return False
    if _is_http_url(raw):
        return True
    from app.rag.original_docs import looks_like_object_ref

    if looks_like_object_ref(raw):
        return True
    parsed = urlparse(raw)
    if parsed.scheme:
        return False
    return raw.startswith("rag-docs/") or "/" in raw
