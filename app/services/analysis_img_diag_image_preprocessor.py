"""看图诊断专用图片预处理：保细节导向（与智能客服缩图策略相反）。"""

from __future__ import annotations

import io
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, List
from urllib.parse import urlparse

import httpx

from app.core.config import get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from PIL import Image, ImageEnhance, ImageFilter
except Exception:  # noqa: BLE001
    Image = None  # type: ignore[assignment,misc]
    ImageEnhance = None  # type: ignore[assignment,misc]
    ImageFilter = None  # type: ignore[assignment,misc]

try:
    from minio import Minio
except Exception:  # noqa: BLE001
    Minio = None  # type: ignore[assignment]


class AnalysisImgDiagImagePreprocessor:
    """
    缺陷/爆口图像送 VL 前预处理：
    - 过小图放大至 min_edge（保留细节供量化 VL 识别）
    - 过大图缩小至 max_edge（避免 vLLM 过度下采样）
    - 高质量 JPEG + 可选锐化/对比度增强
    """

    def __init__(self) -> None:
        cfg = get_app_config().analysis
        chat = get_app_config().chatbot
        self._enabled = bool(cfg.img_diag_vision_preprocess_enabled)
        self._min_edge = max(512, int(cfg.img_diag_vision_min_edge))
        self._max_edge = max(self._min_edge, int(cfg.img_diag_vision_max_edge))
        self._jpeg_quality = max(85, min(98, int(cfg.img_diag_vision_jpeg_quality)))
        self._sharpen = bool(cfg.img_diag_vision_sharpen_enabled)
        self._contrast = max(1.0, min(1.4, float(cfg.img_diag_vision_contrast_factor)))
        self._public_path = self._normalize_public_path(chat.image_public_path)
        self._store_dir = self._resolve_store_dir(chat.image_store_dir)
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._storage_backend = (chat.image_storage_backend or "minio").strip().lower()
        self._minio_bucket = (chat.image_minio_bucket or "chatbot-images").strip()
        self._minio_presign_ttl = max(300, int(chat.image_minio_presign_ttl_seconds))
        self._minio = self._init_minio(chat)

    async def preprocess_urls(self, urls: List[str]) -> List[str]:
        cleaned = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
        if not cleaned or not self._enabled or Image is None:
            if not self._enabled:
                return cleaned
            if Image is None:
                logger.warning("img_diag vision preprocess skipped: Pillow unavailable")
            return cleaned

        out: List[str] = []
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            for u in cleaned:
                try:
                    out.append(await self._preprocess_one(client, u))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("img_diag image preprocess failed url=%s err=%s", u[:120], exc)
                    out.append(u)
        return out

    async def _preprocess_one(self, client: httpx.AsyncClient, url: str) -> str:
        if url.startswith(self._public_path + "/") or url.startswith(self._public_path):
            return url
        scheme = (urlparse(url).scheme or "").lower()
        if scheme not in {"http", "https"}:
            return url

        resp = await client.get(url)
        resp.raise_for_status()
        raw = resp.content
        if not raw:
            return url

        with Image.open(io.BytesIO(raw)) as im:  # type: ignore[union-attr]
            im = self._to_rgb(im)
            w, h = im.size
            long_edge = max(w, h)
            if long_edge < self._min_edge:
                scale = self._min_edge / float(long_edge)
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)  # type: ignore[union-attr]
            elif long_edge > self._max_edge:
                im.thumbnail((self._max_edge, self._max_edge), Image.Resampling.LANCZOS)  # type: ignore[union-attr]
            if self._contrast > 1.0 and ImageEnhance is not None:
                im = ImageEnhance.Contrast(im).enhance(self._contrast)
            if self._sharpen and ImageFilter is not None:
                im = im.filter(ImageFilter.SHARPEN)
            buf = io.BytesIO()
            im.save(buf, format="JPEG", optimize=True, quality=self._jpeg_quality)
            out_bytes = buf.getvalue()

        file_name = f"img_diag_{uuid.uuid4().hex}.jpg"
        if self._storage_backend == "minio":
            minio_url = self._upload_to_minio(file_name=file_name, content=out_bytes)
            if minio_url:
                return minio_url
        out_path = self._store_dir / file_name
        out_path.write_bytes(out_bytes)
        return f"{self._public_path}/{file_name}"

    def _init_minio(self, chat_cfg: Any) -> Any | None:
        if Minio is None or self._storage_backend != "minio":
            return None
        endpoint = (chat_cfg.image_minio_endpoint or "").strip()
        if not endpoint:
            return None
        try:
            return Minio(
                endpoint,
                access_key=(chat_cfg.image_minio_access_key or "").strip(),
                secret_key=(chat_cfg.image_minio_secret_key or "").strip(),
                secure=bool(chat_cfg.image_minio_secure),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag minio init failed: %s", exc)
            return None

    def _upload_to_minio(self, *, file_name: str, content: bytes) -> str | None:
        if self._minio is None:
            return None
        object_name = f"analysis_img_diag/processed/{file_name}"
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
                expires=timedelta(seconds=self._minio_presign_ttl),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("img_diag minio upload failed: %s", exc)
            return None

    @staticmethod
    def _to_rgb(im: "Image.Image") -> "Image.Image":
        if im.mode == "RGB":
            return im
        if im.mode in {"RGBA", "LA"}:
            bg = Image.new("RGB", im.size, (255, 255, 255))  # type: ignore[union-attr]
            bg.paste(im, mask=im.split()[-1])
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
        app_root = Path(__file__).resolve().parents[1]
        return (app_root / p).resolve()
