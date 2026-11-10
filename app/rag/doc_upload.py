"""
RAG 原始文档上传到 MinIO，返回**长期有效**的可访问 URL（非预签名）。

用途：将返回的 ``url`` 作为摄入 ``content`` / ``source_uri``，供后续引用预览打开原件。
"""

from __future__ import annotations

import io
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from minio import Minio
except Exception:  # noqa: BLE001
    Minio = None  # type: ignore[assignment,misc]

_ALLOWED_EXT_TO_SOURCE: dict[str, tuple[str, str]] = {
    ".pdf": ("pdf", "application/pdf"),
    ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".doc": ("doc", "application/msword"),
    ".xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ".xlsm": ("xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12"),
    ".md": ("markdown", "text/markdown"),
    ".markdown": ("markdown", "text/markdown"),
    ".txt": ("text", "text/plain"),
    ".html": ("html", "text/html"),
    ".htm": ("html", "text/html"),
    ".csv": ("text", "text/csv"),
}


@dataclass(frozen=True)
class RagDocumentUploadResult:
    file_name: str
    object_name: str
    bucket: str
    url: str
    content_type: str
    source_type: str
    size_bytes: int


def _safe_filename(name: str) -> str:
    base = Path(name or "document.bin").name
    base = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", base, flags=re.UNICODE)
    return (base or "document.bin")[:180]


def _safe_path_segment(value: str | None, *, fallback: str = "") -> str:
    raw = (value or "").strip()
    if not raw:
        return fallback
    cleaned = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", raw, flags=re.UNICODE).strip("._")
    return (cleaned or fallback)[:80]


def guess_source_and_content_type(
    file_name: str,
    content_type: str | None,
) -> tuple[str, str]:
    suf = Path(file_name).suffix.lower()
    if suf in _ALLOWED_EXT_TO_SOURCE:
        source_type, default_ct = _ALLOWED_EXT_TO_SOURCE[suf]
        ct = (content_type or "").strip() or default_ct
        return source_type, ct
    raise ValueError(
        "unsupported file type; allowed: "
        + ", ".join(sorted({k for k in _ALLOWED_EXT_TO_SOURCE}))
    )


def _build_minio_client(chat_cfg: Any) -> Any:
    if Minio is None:
        raise RuntimeError("MinIO client unavailable; install minio package")
    endpoint = (chat_cfg.image_minio_endpoint or "").strip()
    access = (chat_cfg.image_minio_access_key or "").strip()
    secret = (chat_cfg.image_minio_secret_key or "").strip()
    if not endpoint or not access or not secret:
        raise RuntimeError("CHATBOT_IMAGE_MINIO_* is not fully configured")
    return Minio(
        endpoint,
        access_key=access,
        secret_key=secret,
        secure=bool(chat_cfg.image_minio_secure),
    )


def _build_permanent_url(
    *,
    public_base_url: str,
    endpoint: str,
    secure: bool,
    bucket: str,
    object_name: str,
) -> str:
    """
    构造长期 URL（无签名查询参数）。

    - 优先 ``RAG_DOC_PUBLIC_BASE_URL``（对外可访问的网关/反代/公网 MinIO 前缀）；
    - 否则 ``http(s)://{endpoint}/{bucket}/{object}``（适合同网段直连 MinIO）。
    """
    key = object_name.lstrip("/")
    # 对 path 分段编码，保留斜杠
    encoded_key = "/".join(quote(seg, safe="") for seg in key.split("/"))
    base = (public_base_url or "").strip().rstrip("/")
    if base:
        return f"{base}/{encoded_key}"
    scheme = "https" if secure else "http"
    ep = (endpoint or "").strip().rstrip("/")
    return f"{scheme}://{ep}/{bucket}/{encoded_key}"


def _ensure_public_read(client: Any, bucket: str, prefix: str) -> None:
    """
    为 bucket 设置匿名 GetObject（供长期 URL 无需签名访问）。

    说明：会覆盖该 bucket 上既有 policy；请使用独立 ``RAG_DOC_MINIO_BUCKET``。
    """
    prefix = (prefix or "").lstrip("/")
    resource = f"arn:aws:s3:::{bucket}/{prefix}*" if prefix else f"arn:aws:s3:::{bucket}/*"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [resource],
            }
        ],
    }
    try:
        client.set_bucket_policy(bucket, json.dumps(policy))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "set_bucket_policy failed bucket=%s prefix=%s err=%s; "
            "permanent URL may 403 until ops grants anonymous GetObject",
            bucket,
            prefix,
            exc,
        )


def upload_rag_document(
    *,
    file_name: str,
    content: bytes,
    content_type: str | None = None,
    namespace: str | None = None,
    doc_name: str | None = None,
) -> RagDocumentUploadResult:
    """上传文档到 MinIO 并返回长期有效 URL。"""
    if not content:
        raise ValueError("empty file upload")

    app_cfg = get_app_config()
    chat_cfg = app_cfg.chatbot
    ingest_cfg = app_cfg.rag.ingestion

    max_mb = max(1, int(getattr(ingest_cfg, "doc_upload_max_mb", 200) or 200))
    max_bytes = max_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError(f"file exceeds max size {max_mb}MB")

    safe_name = _safe_filename(file_name)
    source_type, put_ct = guess_source_and_content_type(safe_name, content_type)

    bucket = (getattr(ingest_cfg, "doc_minio_bucket", None) or "rag-docs").strip() or "rag-docs"
    prefix = (getattr(ingest_cfg, "doc_object_key_prefix", None) or "docs/").strip()
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    ns_seg = _safe_path_segment(namespace)
    doc_seg = _safe_path_segment(doc_name) or _safe_path_segment(Path(safe_name).stem, fallback="doc")
    object_name = f"{prefix}{ns_seg + '/' if ns_seg else ''}{doc_seg}/{uuid.uuid4().hex}_{safe_name}"

    client = _build_minio_client(chat_cfg)
    if bool(chat_cfg.image_minio_auto_create_bucket) and not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    if bool(getattr(ingest_cfg, "doc_ensure_public_read", True)):
        _ensure_public_read(client, bucket, prefix)

    client.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=io.BytesIO(content),
        length=len(content),
        content_type=put_ct,
    )

    url = _build_permanent_url(
        public_base_url=str(getattr(ingest_cfg, "doc_public_base_url", "") or ""),
        endpoint=str(chat_cfg.image_minio_endpoint or ""),
        secure=bool(chat_cfg.image_minio_secure),
        bucket=bucket,
        object_name=object_name,
    )
    return RagDocumentUploadResult(
        file_name=safe_name,
        object_name=object_name,
        bucket=bucket,
        url=url,
        content_type=put_ct,
        source_type=source_type,
        size_bytes=len(content),
    )
