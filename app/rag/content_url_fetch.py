from __future__ import annotations

import ipaddress
import socket
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import RAGContentFetchConfig, get_app_config
from app.core.logging import get_logger
from app.rag.models import DocumentSource
from app.rag.namespace_kb import clone_document_source

logger = get_logger(__name__)


class ContentFetchError(ValueError):
    """URL 校验或拉取失败（含 SSRF 拒绝）。"""


def looks_like_http_url(content: str) -> bool:
    s = (content or "").strip().lower()
    return s.startswith("http://") or s.startswith("https://")


def _guess_suffix_from_url(url: str, content_type: str | None) -> str:
    path = urlparse(url).path.lower()
    for ext in (".pdf", ".docx", ".doc", ".xlsx", ".xlsm"):
        if path.endswith(ext):
            return ext
    ct = (content_type or "").split(";")[0].strip().lower()
    if "pdf" in ct:
        return ".pdf"
    if "wordprocessingml" in ct or "msword" in ct:
        return ".docx"
    if "spreadsheetml" in ct:
        return ".xlsx"
    if "png" in ct:
        return ".png"
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "webp" in ct:
        return ".webp"
    if "gif" in ct:
        return ".gif"
    return ".bin"


def _host_allowed(host: str, allow_hosts: list[str]) -> bool:
    if not allow_hosts:
        return True
    h = host.lower().rstrip(".")
    for pattern in allow_hosts:
        p = pattern.strip().lower().rstrip(".")
        if not p:
            continue
        if p.startswith("*."):
            root = p[2:]
            if h == root or h.endswith("." + root):
                return True
        elif h == p:
            return True
    return False


def _validate_resolved_ips(host: str, block_private: bool) -> None:
    if not block_private:
        return
    hl = host.lower()
    if hl == "localhost" or hl.endswith(".localhost"):
        raise ContentFetchError("host not allowed: localhost")
    if hl.endswith(".local") and not hl.endswith(".localhost"):
        raise ContentFetchError("host not allowed: .local")

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ContentFetchError(f"DNS resolution failed for host={host!r}: {e}") from e

    for _fam, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ContentFetchError(
                f"resolved address {ip_str} for host={host!r} is not allowed (SSRF / metadata protection)"
            )


def validate_fetch_url(url: str, cfg: RAGContentFetchConfig) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ContentFetchError("only http/https URLs are allowed in content")
    host = parsed.hostname
    if not host:
        raise ContentFetchError("URL has no hostname")
    if not _host_allowed(host, cfg.allow_hosts):
        raise ContentFetchError(
            f"host {host!r} is not allowed; set RAG_CONTENT_FETCH_ALLOW_HOSTS (comma-separated)"
        )
    _validate_resolved_ips(host, cfg.block_private_ips)


def _build_headers(cfg: RAGContentFetchConfig) -> dict[str, str]:
    h: dict[str, str] = {}
    if cfg.bearer_token:
        h["Authorization"] = f"Bearer {cfg.bearer_token}"
    if cfg.header_name and cfg.header_value is not None:
        h[cfg.header_name] = cfg.header_value
    return h


def _stream_download(
    client: httpx.Client,
    url: str,
    cfg: RAGContentFetchConfig,
    *,
    max_redirects: int = 8,
) -> tuple[bytes, str | None]:
    """跟随重定向，每跳校验 URL；返回 body 与最终 Content-Type。"""
    current = url
    headers = _build_headers(cfg)
    final_ct: str | None = None

    for _hop in range(max_redirects + 1):
        validate_fetch_url(current, cfg)
        resp = client.get(current, headers=headers, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location")
            if not loc:
                raise ContentFetchError(f"redirect without Location from {current!r}")
            current = urljoin(current, loc.strip())
            continue
        resp.raise_for_status()
        final_ct = resp.headers.get("content-type")
        total = 0
        chunks: list[bytes] = []
        for chunk in resp.iter_bytes():
            if not chunk:
                continue
            total += len(chunk)
            if total > cfg.max_bytes:
                raise ContentFetchError(
                    f"response larger than RAG_CONTENT_FETCH_MAX_BYTES ({cfg.max_bytes})"
                )
            chunks.append(chunk)
        return b"".join(chunks), final_ct

    raise ContentFetchError("too many redirects")


def fetch_url_bytes(url: str, cfg: RAGContentFetchConfig) -> tuple[bytes, str | None]:
    timeout = httpx.Timeout(cfg.timeout_s, connect=min(30.0, cfg.timeout_s))
    limits = httpx.Limits(max_keepalive_connections=2, max_connections=4)
    with httpx.Client(timeout=timeout, limits=limits) as client:
        return _stream_download(client, url, cfg)


def _should_fetch_as_file(source_type: str) -> bool:
    st = (source_type or "text").lower()
    return st in {"pdf", "docx", "doc", "xlsx", "xlsm", "image", "png", "jpg", "jpeg", "webp", "gif"}


def _sniff_binary_source_type(data: bytes, content_type: str | None, url: str) -> str | None:
    """当 source_type 为 text 但响应体实为 PDF/Office 时，推断正确类型。"""
    if data.startswith(b"%PDF-"):
        return "pdf"
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/pdf":
        return "pdf"
    if len(data) >= 4 and data[:2] == b"PK":
        path = urlparse(url).path.lower()
        if path.endswith(".docx") or "wordprocessingml" in ct or "msword" in ct:
            return "docx"
        if path.endswith(".xlsx") or path.endswith(".xlsm") or "spreadsheetml" in ct:
            return "xlsx"
    return None


def normalize_document_source_type(doc: DocumentSource) -> DocumentSource:
    """
    本地路径 + 错误 source_type 时自动纠正；内联二进制乱码则拒绝摄入。
    """
    from app.rag.document_pipeline.parsers import DocumentParser
    from app.rag.text_quality import looks_like_binary_text

    raw = (doc.content or "").strip()
    st = (doc.source_type or "text").lower()
    if st in {"text", "txt", "markdown", "md", "html"}:
        if looks_like_binary_text(raw):
            raise ContentFetchError(
                "content looks like binary data stored as text; "
                "set source_type=pdf/docx or re-ingest via file URL with correct source_type"
            )
        p = DocumentParser.resolve_local_path(raw)
        if p is not None:
            ext_map = {
                ".pdf": "pdf",
                ".docx": "docx",
                ".doc": "doc",
                ".xlsx": "xlsx",
                ".xlsm": "xlsx",
            }
            guessed = ext_map.get(p.suffix.lower())
            if guessed and guessed != st:
                logger.info(
                    "normalize source_type doc_name=%s path=%s %s -> %s",
                    doc.doc_name,
                    p,
                    st,
                    guessed,
                )
                return clone_document_source(doc, source_type=guessed, content=str(p.resolve()))
    return doc


def _materialize_bytes_as_temp_file(
    doc: DocumentSource,
    data: bytes,
    ct: str | None,
    raw_url: str,
) -> tuple[DocumentSource, Path]:
    suffix = _guess_suffix_from_url(raw_url, ct)
    fd, path_str = tempfile.mkstemp(prefix="rag_fetch_", suffix=suffix)
    path = Path(path_str)
    try:
        import os

        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise

    meta = {
        **doc.metadata,
        "content_fetched_from_url": raw_url,
        "content_fetch_content_type": ct,
    }
    new_doc = clone_document_source(
        doc,
        content=str(path.resolve()),
        source_uri=doc.source_uri or raw_url,
        metadata=meta,
    )
    logger.info(
        "content URL fetched to temp file doc_name=%s url=%s path=%s bytes=%s source_type=%s",
        doc.doc_name,
        raw_url,
        path,
        len(data),
        doc.source_type,
    )
    return new_doc, path


def materialize_document_content_from_url(doc: DocumentSource) -> tuple[DocumentSource, Path | None]:
    """
    若 `RAG_CONTENT_FETCH_ENABLED` 且 `content` 为 http(s) URL，则拉取并落地：
    - pdf/doc/docx/xlsx：写入临时文件，将 `content` 替换为本地路径（供 MinerU / pypdf / docx / openpyxl 使用）；
    - 其它 source_type：将响应体按 UTF-8（失败则 latin-1）解码为字符串写入 `content`。

    返回 (新 DocumentSource, 临时文件路径或 None)。调用方须在 `finally` 中 `unlink` 临时文件。
    """
    from app.rag.original_docs import (
        looks_like_object_ref,
        materialize_document_content_from_object_ref,
        merge_existing_original_metadata,
    )

    doc = merge_existing_original_metadata(doc)
    doc = normalize_document_source_type(doc)
    raw = (doc.content or "").strip()
    if looks_like_object_ref(raw) or looks_like_object_ref(doc.source_uri):
        return materialize_document_content_from_object_ref(doc)

    cfg = get_app_config().rag.content_fetch
    if not cfg.enabled or not looks_like_http_url(raw):
        return doc, None

    if not _should_fetch_as_file(doc.source_type):
        try:
            data, ct = fetch_url_bytes(raw, cfg)
        except ContentFetchError:
            raise
        except httpx.HTTPError as e:
            raise ContentFetchError(f"HTTP fetch failed: {e}") from e

        sniffed = _sniff_binary_source_type(data, ct, raw)
        if sniffed:
            logger.warning(
                "content URL response is binary (%s) but source_type=%s; auto-routing to file parser doc_name=%s url=%s",
                sniffed,
                doc.source_type,
                doc.doc_name,
                raw,
            )
            routed = clone_document_source(doc, source_type=sniffed)
            new_doc, path = _materialize_bytes_as_temp_file(routed, data, ct, raw)
            return new_doc, path

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1", errors="replace")

        meta = {
            **doc.metadata,
            "content_fetched_from_url": raw,
            "content_fetch_content_type": ct,
        }
        new_doc = clone_document_source(
            doc,
            content=text,
            source_uri=doc.source_uri or raw,
            metadata=meta,
        )
        logger.info(
            "content URL fetched as inline text doc_name=%s url=%s bytes=%s ctype=%s",
            doc.doc_name,
            raw,
            len(data),
            ct,
        )
        return new_doc, None

    try:
        data, ct = fetch_url_bytes(raw, cfg)
    except ContentFetchError:
        raise
    except httpx.HTTPError as e:
        raise ContentFetchError(f"HTTP fetch failed: {e}") from e

    new_doc, path = _materialize_bytes_as_temp_file(doc, data, ct, raw)
    return new_doc, path
