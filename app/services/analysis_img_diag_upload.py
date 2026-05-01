from __future__ import annotations

import io
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.core.config import get_app_config
from app.models.inspection_extract import InspectionUploadResponse

try:
    from minio import Minio
except Exception:  # noqa: BLE001
    Minio = None  # type: ignore[assignment]

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def _guess_content_type(file_name: str, content_type: str | None) -> str:
    ct = (content_type or "").strip().lower()
    if "jpeg" in ct or "jpg" in ct:
        return "image/jpeg"
    if "png" in ct:
        return "image/png"
    if "webp" in ct:
        return "image/webp"
    suf = Path(file_name).suffix.lower()
    if suf in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suf == ".png":
        return "image/png"
    if suf == ".webp":
        return "image/webp"
    return ct or "application/octet-stream"


def validate_img_diag_upload(*, file_name: str, content: bytes, content_type: str | None) -> str:
    cfg = get_app_config().analysis
    max_bytes = max(1024 * 1024, int(cfg.img_diag_upload_max_mb) * 1024 * 1024)
    if len(content) > max_bytes:
        raise ValueError(f"image exceeds max size {cfg.img_diag_upload_max_mb}MB")
    ct = _guess_content_type(file_name, content_type)
    suf = Path(file_name).suffix.lower()
    looks_like_image = ct.startswith("image/") and any(x in ct for x in ("jpeg", "jpg", "png", "webp"))
    if not looks_like_image and suf in _ALLOWED_EXT:
        ct = _guess_content_type(file_name, None)
        looks_like_image = ct.startswith("image/") and any(x in ct for x in ("jpeg", "jpg", "png", "webp"))
    if not looks_like_image:
        raise ValueError("only jpeg/png/webp images are allowed for img_diag upload")
    return ct


def _build_minio_client(chat_cfg: Any) -> Any | None:
    if Minio is None:
        return None
    endpoint = (chat_cfg.image_minio_endpoint or "").strip()
    if not endpoint:
        return None
    secure = bool(chat_cfg.image_minio_secure)
    return Minio(
        endpoint=endpoint,
        access_key=(chat_cfg.image_minio_access_key or "").strip(),
        secret_key=(chat_cfg.image_minio_secret_key or "").strip(),
        secure=secure,
    )


async def upload_analysis_img_diag_image(*, file_name: str, content: bytes, content_type: str | None) -> InspectionUploadResponse:
    """上传看图诊断随手拍图片至 MinIO（与智能客服上传共用 bucket 配置，前缀 analysis_img_diag/）。"""
    ct = validate_img_diag_upload(file_name=file_name, content=content, content_type=content_type)
    chat_cfg = get_app_config().chatbot
    bucket = (chat_cfg.image_minio_bucket or "chatbot-images").strip()
    ttl = max(300, int(chat_cfg.image_minio_presign_ttl_seconds))
    client = _build_minio_client(chat_cfg)
    if client is None:
        raise RuntimeError("MinIO client unavailable; check minio dependency and CHATBOT_IMAGE_MINIO_* config")

    safe_name = Path(file_name).name or "image.bin"
    object_name = f"analysis_img_diag/{uuid.uuid4().hex}_{safe_name}"
    client.put_object(
        bucket_name=bucket,
        object_name=object_name,
        data=io.BytesIO(content),
        length=len(content),
        content_type=ct,
    )
    url = client.presigned_get_object(bucket_name=bucket, object_name=object_name, expires=timedelta(seconds=ttl))
    return InspectionUploadResponse(
        ok=True,
        file_name=safe_name,
        object_name=object_name,
        source_type="image",
        url=url,
        bucket=bucket,
    )
