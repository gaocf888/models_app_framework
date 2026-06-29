from __future__ import annotations

import io
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.core.config import ChatbotConfig, get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from minio import Minio
except Exception:  # noqa: BLE001
    Minio = None  # type: ignore[assignment,misc]


class RagAssetStorage:
    """知识库 figure 图片存储（复用 Chatbot MinIO 连接，独立 bucket）。"""

    def __init__(self) -> None:
        app_cfg = get_app_config()
        self._ingest = app_cfg.rag.ingestion
        self._chatbot: ChatbotConfig = app_cfg.chatbot
        self._backend = (self._chatbot.image_storage_backend or "minio").strip().lower()
        self._bucket = (self._ingest.figure_minio_bucket or "rag-assets").strip()
        self._prefix = (self._ingest.figure_object_key_prefix or "rag-assets/").strip()
        if self._prefix and not self._prefix.endswith("/"):
            self._prefix += "/"
        self._presign_ttl = max(60, int(self._ingest.figure_presign_ttl_seconds))
        self._minio = None
        self._local_dir = self._resolve_local_dir(self._chatbot.image_store_dir)
        if self._backend == "minio":
            self._init_minio()

    @staticmethod
    def _resolve_local_dir(v: str) -> Path:
        p = Path((v or "runtime/rag_assets").strip())
        if not p.is_absolute():
            app_root = Path(__file__).resolve().parents[1]
            p = (app_root / "runtime" / "rag_assets").resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _init_minio(self) -> None:
        if Minio is None:
            logger.warning("minio package missing; rag figure storage falls back to local")
            return
        endpoint = (self._chatbot.image_minio_endpoint or "").strip()
        access = (self._chatbot.image_minio_access_key or "").strip()
        secret = (self._chatbot.image_minio_secret_key or "").strip()
        if not endpoint or not access or not secret:
            logger.warning("minio config incomplete for rag assets; using local storage")
            return
        try:
            self._minio = Minio(
                endpoint,
                access_key=access,
                secret_key=secret,
                secure=bool(self._chatbot.image_minio_secure),
            )
            if self._chatbot.image_minio_auto_create_bucket and not self._minio.bucket_exists(self._bucket):
                self._minio.make_bucket(self._bucket)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rag asset minio init failed: %s", exc)
            self._minio = None

    def _object_key(self, *, doc_name: str, doc_version: str, figure_index: int, suffix: str) -> str:
        safe_doc = "".join(c if c.isalnum() or c in "-_." else "_" for c in doc_name)[:120]
        ext = suffix if suffix.startswith(".") else f".{suffix}"
        return f"{self._prefix}{safe_doc}/{doc_version}/fig_{figure_index:04d}{ext}"

    def upload_image(
        self,
        *,
        local_path: str | None = None,
        data: bytes | None = None,
        doc_name: str,
        doc_version: str = "v1",
        figure_index: int = 0,
        content_type: str = "image/png",
    ) -> dict[str, str]:
        if local_path:
            p = Path(local_path)
            if not p.is_file():
                raise FileNotFoundError(f"image not found: {local_path}")
            data = p.read_bytes()
            suffix = p.suffix.lower() or ".png"
        elif data:
            suffix = ".png"
        else:
            raise ValueError("upload_image requires local_path or data")

        object_key = self._object_key(
            doc_name=doc_name, doc_version=doc_version, figure_index=figure_index, suffix=suffix
        )

        if self._backend == "minio" and self._minio is not None:
            self._minio.put_object(
                bucket_name=self._bucket,
                object_name=object_key,
                data=io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
            url = self._minio.presigned_get_object(
                bucket_name=self._bucket,
                object_name=object_key,
                expires=timedelta(seconds=self._presign_ttl),
            )
            return {"image_url": url, "image_object_key": object_key}

        fname = f"{uuid.uuid4().hex}{suffix}"
        out = self._local_dir / fname
        out.write_bytes(data)
        public = (self._chatbot.image_public_path or "/rag/media").rstrip("/")
        return {
            "image_url": f"{public}/{fname}",
            "image_object_key": str(out),
        }

    def presign_get_url(self, object_key: str) -> str:
        """按 MinIO object key 重新签发预签名 GET URL（供前端刷新过期链接）。"""
        key = (object_key or "").strip().lstrip("/")
        if not key:
            raise ValueError("object_key is required")
        if self._minio is None:
            p = Path(key)
            if p.is_file():
                public = (self._chatbot.image_public_path or "/rag/media").rstrip("/")
                return f"{public}/{p.name}"
            raise ValueError("presign requires minio backend or local file path")
        return self._minio.presigned_get_object(
            bucket_name=self._bucket,
            object_name=key,
            expires=timedelta(seconds=self._presign_ttl),
        )

    def delete_by_doc(self, doc_name: str, doc_version: str | None = None) -> int:
        """按 doc 前缀删除 MinIO 对象；local 模式尽力删除。"""
        deleted = 0
        safe_doc = "".join(c if c.isalnum() or c in "-_." else "_" for c in doc_name)[:120]
        prefix = f"{self._prefix}{safe_doc}/"
        if doc_version:
            prefix = f"{prefix}{doc_version}/"
        if self._minio is not None:
            try:
                for obj in self._minio.list_objects(self._bucket, prefix=prefix, recursive=True):
                    self._minio.remove_object(self._bucket, obj.object_name)
                    deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("delete rag assets failed prefix=%s err=%s", prefix, exc)
        return deleted

    @staticmethod
    def is_http_url(value: str) -> bool:
        s = (value or "").strip().lower()
        return s.startswith("http://") or s.startswith("https://")

    def ensure_image_url(
        self,
        *,
        content: str,
        doc_name: str,
        doc_version: str = "v1",
        figure_index: int = 0,
    ) -> dict[str, str]:
        """content 为本地路径、file:// 或 http(s) URL。"""
        raw = (content or "").strip()
        if self.is_http_url(raw):
            return {"image_url": raw, "image_object_key": raw}
        from app.rag.document_pipeline.parsers import DocumentParser

        p = DocumentParser.resolve_local_path(raw) or Path(raw)
        if p.is_file():
            ct = "image/jpeg" if p.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            return self.upload_image(
                local_path=str(p),
                doc_name=doc_name,
                doc_version=doc_version,
                figure_index=figure_index,
                content_type=ct,
            )
        parsed = urlparse(raw)
        if parsed.scheme in {"http", "https"}:
            return {"image_url": raw, "image_object_key": raw}
        raise FileNotFoundError(f"cannot resolve image content: {raw[:200]}")
