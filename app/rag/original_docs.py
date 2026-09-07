"""原文对象引用（minio:// / local:）与管理面 namespace 校验。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.models import DocumentSource
from app.rag.namespace_kb import clone_document_source, resolve_namespace_kb_fields

logger = get_logger(__name__)

META_OBJECT_KEY = "object_key"
META_FILE_SIZE = "file_size"
META_ORIGINAL_FILENAME = "original_filename"
DOC_STATUS_UPLOADED = "UPLOADED"
MINIO_URI_PREFIX = "minio://"
LOCAL_URI_PREFIX = "local:"

# Swagger UI / OpenAPI Try-it-out 常把可选 Form 字段预填为类型名；勿当作真实业务值
_SWAGGER_FORM_PLACEHOLDERS = frozenset(
    {
        "string",
        "str",
        "null",
        "none",
        "undefined",
        "object",
        "integer",
        "number",
        "boolean",
        "array",
    }
)


def sanitize_optional_form_str(value: str | None) -> str | None:
    """
    清洗可选 multipart Form 字符串：空串与 Swagger 占位符视为未传。
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in _SWAGGER_FORM_PLACEHOLDERS:
        return None
    return s


class OriginalObjectError(ValueError):
    """对象存储原文读写失败。"""


def namespace_is_blank(namespace: str | None) -> bool:
    return namespace is None or not str(namespace).strip()


def require_namespace_enabled() -> bool:
    return bool(getattr(get_app_config().rag, "require_namespace", False))


def normalize_required_namespace(namespace: str | None, *, always: bool = False) -> str:
    """去空格后的 namespace；always 或 RAG_REQUIRE_NAMESPACE 时禁止为空。"""
    ns = (namespace or "").strip()
    if always or require_namespace_enabled():
        if not ns:
            raise ValueError("namespace is required")
        return ns
    return ns


def guess_source_type(filename: str, content_type: str | None = None) -> str:
    suffix = Path(filename or "").suffix.lower()
    ext_map = {
        ".pdf": "pdf",
        ".docx": "docx",
        ".doc": "doc",
        ".xlsx": "xlsx",
        ".xlsm": "xlsx",
        ".md": "markdown",
        ".markdown": "markdown",
        ".html": "html",
        ".htm": "html",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".webp": "image",
        ".gif": "image",
        ".txt": "text",
    }
    if suffix in ext_map:
        return ext_map[suffix]
    ct = (content_type or "").split(";")[0].strip().lower()
    if "pdf" in ct:
        return "pdf"
    if "wordprocessingml" in ct or "msword" in ct:
        return "docx"
    if "spreadsheetml" in ct:
        return "xlsx"
    if ct.startswith("image/"):
        return "image"
    if "markdown" in ct:
        return "markdown"
    if "html" in ct:
        return "html"
    return "text"


def looks_like_object_ref(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return False
    if raw.startswith(MINIO_URI_PREFIX) or raw.startswith(LOCAL_URI_PREFIX):
        return True
    prefix = (getattr(get_app_config().rag.ingestion, "original_object_key_prefix", None) or "rag-docs/").strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return bool(prefix) and raw.startswith(prefix)


def parse_object_ref(value: str) -> tuple[str, str | None, str]:
    """
    解析对象引用。
    返回 (kind, bucket_or_none, key_or_path)，kind 为 minio | local | key。
    """
    raw = (value or "").strip()
    if raw.startswith(MINIO_URI_PREFIX):
        parsed = urlparse(raw)
        bucket = unquote(parsed.netloc or "")
        key = unquote((parsed.path or "").lstrip("/"))
        if not bucket or not key:
            raise OriginalObjectError(f"invalid minio object uri: {raw[:200]}")
        return "minio", bucket, key
    if raw.startswith(LOCAL_URI_PREFIX):
        path = raw[len(LOCAL_URI_PREFIX) :].strip()
        if path.startswith("//"):
            path = path[2:]
        if not path:
            raise OriginalObjectError("invalid local object uri")
        return "local", None, path
    return "key", None, raw


def resolve_namespace_kb_for_ingest(
    namespace: str | None,
    enabled: bool | None,
    priority: int | None,
) -> tuple[bool, int]:
    """未传 kb 字段时继承该 namespace 已有配置。"""
    if enabled is not None or priority is not None:
        return resolve_namespace_kb_fields(enabled, priority)
    try:
        from app.rag.document_repository import DocumentRepository

        rows = DocumentRepository().list_namespace_kb_configs()
    except Exception:  # noqa: BLE001
        logger.warning("inherit namespace kb config failed; using defaults", exc_info=True)
        return resolve_namespace_kb_fields(None, None)
    ns_key = None if namespace_is_blank(namespace) else str(namespace).strip()
    for row in rows:
        if row.get("namespace") == ns_key:
            return bool(row.get("namespace_kb_enabled", True)), int(row.get("namespace_kb_priority") or 1)
    return resolve_namespace_kb_fields(None, None)


def merge_existing_original_metadata(doc: DocumentSource) -> DocumentSource:
    """摄入时若未带 object_key，从已有 docs 登记补全，便于只传 content=对象 URI。"""
    if looks_like_object_ref(doc.content) or looks_like_object_ref(doc.source_uri):
        meta = dict(doc.metadata or {})
        if meta.get(META_OBJECT_KEY) and meta.get(META_FILE_SIZE) is not None:
            return doc
        try:
            from app.rag.document_repository import DocumentRepository, make_document_storage_key

            cfg = get_app_config().rag
            fallback = cfg.ingestion.tenant_id_default or "default"
            key = make_document_storage_key(
                doc.doc_name,
                namespace=doc.namespace,
                tenant_id=doc.tenant_id,
                doc_version=doc.doc_version,
                tenant_id_fallback=fallback,
            )
            existing = DocumentRepository().get(key) or {}
            exist_meta = dict(existing.get("metadata") or {})
            for k in (META_OBJECT_KEY, META_FILE_SIZE, META_ORIGINAL_FILENAME):
                if meta.get(k) is None and exist_meta.get(k) is not None:
                    meta[k] = exist_meta[k]
            source_uri = doc.source_uri or existing.get("source_uri")
            source_type = doc.source_type
            if (source_type or "text").lower() == "text" and existing.get("source_type"):
                source_type = existing.get("source_type")
            return clone_document_source(doc, metadata=meta, source_uri=source_uri, source_type=source_type)
        except Exception:  # noqa: BLE001
            logger.warning("merge existing original metadata failed doc=%s", doc.doc_name, exc_info=True)
    return doc


def materialize_document_content_from_object_ref(
    doc: DocumentSource,
) -> tuple[DocumentSource, Path | None]:
    """将 minio:// / local: / 对象键物化为本地临时文件，供解析管线使用。"""
    import tempfile

    from app.rag.asset_storage import RagAssetStorage
    from app.rag.content_url_fetch import _should_fetch_as_file

    raw = (doc.content or "").strip() or (doc.source_uri or "").strip() or str((doc.metadata or {}).get(META_OBJECT_KEY) or "")
    if not looks_like_object_ref(raw):
        return doc, None

    storage = RagAssetStorage()
    data, content_type = storage.get_original_bytes(raw)
    st = (doc.source_type or "text").lower()
    if not _should_fetch_as_file(st):
        from app.rag.content_url_fetch import _sniff_binary_source_type

        sniffed = _sniff_binary_source_type(data, content_type, raw)
        if sniffed:
            st = sniffed
        else:
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                text = data.decode("latin-1", errors="replace")
            meta = {**(doc.metadata or {}), "content_fetched_from_object": raw}
            return clone_document_source(doc, content=text, source_type=st, metadata=meta), None

    suffix = Path(raw.replace("\\", "/")).suffix or ".bin"
    if len(suffix) > 8:
        suffix = ".bin"
    fd, path_str = tempfile.mkstemp(prefix="rag_obj_", suffix=suffix)
    path = Path(path_str)
    try:
        import os

        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    meta = {
        **(doc.metadata or {}),
        "content_fetched_from_object": raw,
        "content_fetch_content_type": content_type,
    }
    new_doc = clone_document_source(
        doc,
        content=str(path.resolve()),
        source_type=st,
        source_uri=doc.source_uri or raw,
        metadata=meta,
    )
    logger.info(
        "original object materialized doc=%s ref=%s path=%s bytes=%s source_type=%s",
        doc.doc_name,
        raw[:160],
        path,
        len(data),
        st,
    )
    return new_doc, path


def original_ref_from_record(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    meta = payload.get("metadata") or {}
    for candidate in (meta.get(META_OBJECT_KEY), payload.get("source_uri")):
        if looks_like_object_ref(str(candidate or "")):
            return str(candidate)
    return None
