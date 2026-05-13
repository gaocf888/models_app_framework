"""
检修 V0：调用 paddleocr-layout-api 的生产级 HTTP 客户端。

- GET /health：可配置次数的幂等重试（仅连接/5xx/超时）；
- POST /v1/layout-ocr：单次请求超时 + 总超时与 httpx 对齐；4xx/5xx/JSON 解析失败映射为应用内异常。上传体支持 **PDF、DOC/DOCX（侧车内 LibreOffice 转 PDF）、PNG/JPEG**。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.config import InspectionExtractV0Config
from app.core.logging import get_logger

logger = get_logger(__name__)


class LayoutOcrError(Exception):
    """版面 OCR 调用失败基类。"""


class LayoutOcrTransportError(LayoutOcrError):
    """网络/超时/连接类错误。"""


class LayoutOcrApiError(LayoutOcrError):
    """HTTP 4xx/5xx 或服务端业务错误。"""


class LayoutOcrParseError(LayoutOcrError):
    """响应体非预期 JSON。"""


class LayoutOcrClient:
    def __init__(self, cfg: InspectionExtractV0Config) -> None:
        self._cfg = cfg
        self._base = (cfg.layout_ocr_endpoint or "").rstrip("/")
        self._max_upload_bytes = max(1, int(cfg.layout_ocr_max_upload_mb)) * 1024 * 1024
        total = max(10.0, float(cfg.layout_ocr_timeout_seconds))
        self._httpx_timeout = httpx.Timeout(
            connect=min(60.0, total),
            read=total,
            write=min(total, 600.0),
            pool=total,
        )

    async def health_check(self, *, retries: int = 3, backoff_s: float = 0.4) -> dict[str, Any]:
        url = f"{self._base}/health"
        last_exc: Exception | None = None
        attempts = max(1, int(retries))
        for i in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self._httpx_timeout) as client:
                    resp = await client.get(url)
                if resp.status_code >= 500:
                    raise LayoutOcrApiError(f"layout_ocr health HTTP {resp.status_code}: {(resp.text or '')[:500]}")
                resp.raise_for_status()
                data = resp.json()
                if not isinstance(data, dict):
                    raise LayoutOcrParseError("health response is not a JSON object")
                logger.info("inspection_extract_v0 layout_ocr_health ok attempt=%s", i + 1)
                return data
            except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, LayoutOcrApiError) as exc:
                last_exc = exc
                if i + 1 >= attempts:
                    break
                await asyncio.sleep(backoff_s * (2**i))
        logger.warning("inspection_extract_v0 layout_ocr_health failed err=%s", last_exc)
        raise LayoutOcrTransportError(str(last_exc or "health_check_failed")) from last_exc

    async def layout_ocr_document(self, *, file_bytes: bytes, filename: str, max_pages: int) -> dict[str, Any]:
        """POST /v1/layout-ocr：支持 PDF、DOC/DOCX（侧车转 PDF）、PNG/JPEG。"""
        if len(file_bytes) > self._max_upload_bytes:
            raise LayoutOcrApiError(
                f"upload exceeds INSPECT_EXTRACT_V0_LAYOUT_OCR_MAX_UPLOAD_MB ({self._max_upload_bytes} bytes cap)"
            )
        url = f"{self._base}/v1/layout-ocr"
        params = {"max_pages": max(1, min(50, int(max_pages)))}
        mime = "application/pdf"
        lower = (filename or "").lower()
        if lower.endswith(".docx"):
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif lower.endswith(".doc"):
            mime = "application/msword"
        elif lower.endswith((".png",)):
            mime = "image/png"
        elif lower.endswith((".jpg", ".jpeg")):
            mime = "image/jpeg"
        files = {"file": (filename or "document.bin", file_bytes, mime)}
        try:
            async with httpx.AsyncClient(timeout=self._httpx_timeout) as client:
                resp = await client.post(url, params=params, files=files)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
            logger.warning("inspection_extract_v0 layout_ocr_post transport err=%s", exc)
            raise LayoutOcrTransportError(str(exc)) from exc

        if resp.status_code >= 400:
            body = (resp.text or "")[:4000]
            logger.warning(
                "inspection_extract_v0 layout_ocr_post http=%s body_prefix=%s",
                resp.status_code,
                body[:500],
            )
            try:
                err_json = resp.json()
            except Exception:  # noqa: BLE001
                err_json = None
            if isinstance(err_json, dict):
                raise LayoutOcrApiError(str(err_json))
            raise LayoutOcrApiError(f"HTTP {resp.status_code}: {body[:800]}")

        try:
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise LayoutOcrParseError(f"invalid JSON from layout-ocr: {exc}") from exc
        if not isinstance(data, dict):
            raise LayoutOcrParseError("layout-ocr response is not a JSON object")
        logger.info(
            "inspection_extract_v0 layout_ocr_post ok filename=%s blocks=%s pages=%s",
            filename,
            len(data.get("blocks") or []) if isinstance(data.get("blocks"), list) else 0,
            len(data.get("pages") or []) if isinstance(data.get("pages"), list) else 0,
        )
        return data

    async def layout_ocr_pdf_or_image(self, *, file_bytes: bytes, filename: str, max_pages: int) -> dict[str, Any]:
        """兼容旧名；与 :meth:`layout_ocr_document` 相同。"""
        return await self.layout_ocr_document(file_bytes=file_bytes, filename=filename, max_pages=max_pages)
