from __future__ import annotations

import io
import uuid
from datetime import timedelta
from pathlib import Path
from typing import List
from urllib.parse import urlparse

import httpx

from app.core.config import ChatbotConfig
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from PIL import Image
except Exception:  # noqa: BLE001
    Image = None  # type: ignore[assignment]

try:
    from minio import Minio
except Exception:  # noqa: BLE001
    Minio = None  # type: ignore[assignment]


class ChatbotImagePreprocessor:
    """
    智能客服图片预处理（简化版）。

    目标：
    1) 降低分辨率（最长边限制）；
    2) 大图有损压缩（超过阈值时使用较低 JPEG 质量）；
    3) 落地本地文件并返回可访问 URL（供会话历史展示）。
    """

    def __init__(self, cfg: ChatbotConfig) -> None:
        self._enabled = bool(cfg.image_preprocess_enabled)
        self._max_edge = max(256, int(cfg.image_max_edge))
        self._compress_threshold_bytes = int(max(0.1, float(cfg.image_compress_threshold_mb)) * 1024 * 1024)
        self._jpeg_quality = max(50, min(95, int(cfg.image_jpeg_quality)))
        self._storage_backend = (cfg.image_storage_backend or "minio").strip().lower()
        self._public_path = self._normalize_public_path(cfg.image_public_path)
        self._store_dir = self._resolve_store_dir(cfg.image_store_dir)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._minio = None
        self._minio_bucket = (cfg.image_minio_bucket or "chatbot-images").strip()
        self._minio_presign_ttl_seconds = max(60, int(cfg.image_minio_presign_ttl_seconds))
        if self._storage_backend == "minio":
            self._init_minio(
                endpoint=(cfg.image_minio_endpoint or "").strip(),
                access_key=(cfg.image_minio_access_key or "").strip(),
                secret_key=(cfg.image_minio_secret_key or "").strip(),
                secure=bool(cfg.image_minio_secure),
                auto_create_bucket=bool(cfg.image_minio_auto_create_bucket),
            )

    @property
    def public_path(self) -> str:
        return self._public_path

    async def preprocess_urls(self, urls: List[str]) -> List[str]:
        cleaned = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
        if not cleaned or not self._enabled:
            return cleaned
        if Image is None:
            logger.warning("Pillow not available, skip image preprocessing.")
            return cleaned

        out: List[str] = []
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            for u in cleaned:
                try:
                    out.append(await self._preprocess_one(client, u))
                except Exception as exc:  # noqa: BLE001
                    # 失败不阻断主链路：回退原 URL
                    logger.warning("image preprocess failed, fallback original url=%s err=%s", u, exc)
                    out.append(u)
        return out

    async def _preprocess_one(self, client: httpx.AsyncClient, url: str) -> str:
        # 已是本地文件服务 URL，避免重复处理。
        if url.startswith(self._public_path + "/") or url.startswith(self._public_path):
            return url
        scheme = (urlparse(url).scheme or "").lower()
        if scheme not in {"http", "https"}:
            return url

        resp = await client.get(url)
        resp.raise_for_status()
        content_type = (resp.headers.get("content-type") or "").lower()
        if "image" not in content_type:
            return url
        raw = resp.content
        if not raw:
            return url

        with Image.open(io.BytesIO(raw)) as im:  # type: ignore[union-attr]
            im = self._to_rgb(im)
            im.thumbnail((self._max_edge, self._max_edge), Image.Resampling.LANCZOS)  # type: ignore[union-attr]
            # 超阈值使用配置质量；未超阈值用较高质量尽量保真。
            quality = self._jpeg_quality if len(raw) >= self._compress_threshold_bytes else 92
            out_buf = io.BytesIO()
            im.save(out_buf, format="JPEG", optimize=True, quality=quality)
            out_bytes = out_buf.getvalue()

        file_name = f"{uuid.uuid4().hex}.jpg"
        if self._storage_backend == "minio":
            minio_url = self._upload_to_minio(file_name=file_name, content=out_bytes)
            if minio_url:
                return minio_url
            logger.warning("minio upload unavailable, fallback to local file service path.")
        out_path = self._store_dir / file_name
        out_path.write_bytes(out_bytes)
        return f"{self._public_path}/{file_name}"

    def _init_minio(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        auto_create_bucket: bool,
    ) -> None:
        if Minio is None:
            logger.warning("minio package not installed; image_storage_backend=minio will fallback to local.")
            return
        if not endpoint or not access_key or not secret_key:
            logger.warning("minio config incomplete; image_storage_backend=minio will fallback to local.")
            return
        try:
            self._minio = Minio(
                endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=secure,
            )
            if auto_create_bucket and not self._minio.bucket_exists(self._minio_bucket):
                self._minio.make_bucket(self._minio_bucket)
                logger.info("created minio bucket for chatbot images: %s", self._minio_bucket)
        except Exception as exc:  # noqa: BLE001
            logger.warning("init minio client failed, fallback local storage: %s", exc)
            self._minio = None

    def _upload_to_minio(self, *, file_name: str, content: bytes) -> str | None:
        if self._minio is None:
            return None
        object_name = f"chatbot/{file_name}"
        try:
            self._minio.put_object(
                bucket_name=self._minio_bucket,
                object_name=object_name,
                data=io.BytesIO(content),
                length=len(content),
                content_type="image/jpeg",
            )
            return self._minio.presigned_get_object(
                bucket_name=self._minio_bucket,
                object_name=object_name,
                expires=timedelta(seconds=self._minio_presign_ttl_seconds),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("upload image to minio failed, object=%s err=%s", object_name, exc)
            return None

    @staticmethod
    def _to_rgb(im: "Image.Image") -> "Image.Image":
        if im.mode == "RGB":
            return im
        if im.mode in {"RGBA", "LA"}:
            bg = Image.new("RGB", im.size, (255, 255, 255))  # type: ignore[union-attr]
            alpha = im.split()[-1]
            bg.paste(im, mask=alpha)
            return bg
        return im.convert("RGB")

    @staticmethod
    def _normalize_public_path(v: str) -> str:
        p = (v or "/chatbot/media").strip()
        if not p.startswith("/"):
            p = "/" + p
        return p.rstrip("/")

    @staticmethod
    def _resolve_store_dir(v: str) -> Path:
        p = Path((v or "runtime/chatbot_images").strip())
        if p.is_absolute():
            return p
        # 相对路径统一挂到 app 目录下，便于容器与本地开发对齐。
        app_root = Path(__file__).resolve().parents[1]
        return (app_root / p).resolve()

