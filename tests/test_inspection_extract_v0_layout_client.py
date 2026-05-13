from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import InspectionExtractV0Config
from app.inspection_extract_v0.vision.layout_ocr_client import LayoutOcrApiError, LayoutOcrClient


class _FakeResponse:
    def __init__(self, status_code: int, json_data: object | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("err", request=MagicMock(), response=MagicMock())

    def json(self) -> object:
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class _FakeAsyncClient:
    def __init__(self, *, get_resp: _FakeResponse | None = None, post_resp: _FakeResponse | None = None) -> None:
        self._get_resp = get_resp
        self._post_resp = post_resp

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str) -> _FakeResponse:
        assert self._get_resp is not None
        return self._get_resp

    async def post(self, url: str, **kwargs: object) -> _FakeResponse:
        assert self._post_resp is not None
        return self._post_resp


@pytest.mark.asyncio
async def test_layout_ocr_client_health_ok() -> None:
    cfg = InspectionExtractV0Config(layout_ocr_endpoint="http://127.0.0.1:9")
    client = LayoutOcrClient(cfg)
    resp = _FakeResponse(200, {"status": "ok", "engine": "paddleocr-layout-api", "version": "0.1.0"})
    fake = _FakeAsyncClient(get_resp=resp)

    with patch("httpx.AsyncClient", return_value=fake):
        out = await client.health_check(retries=1)
    assert out["status"] == "ok"


@pytest.mark.asyncio
async def test_layout_ocr_post_json_ok() -> None:
    cfg = InspectionExtractV0Config(layout_ocr_endpoint="http://127.0.0.1:9", layout_ocr_max_upload_mb=32)
    client = LayoutOcrClient(cfg)
    golden = {
        "engine_version": "paddleocr-layout-api/0.1.0",
        "ocr_engine": "paddleocr",
        "layout_engine": "paddleocr-det-rec",
        "pages": [{"page_no": 1, "width": 100, "height": 200, "ocr_latency_ms": 10}],
        "blocks": [
            {
                "block_id": "p1-L0",
                "type": "text",
                "page_no": 1,
                "text": "试",
                "confidence": 0.99,
                "bbox": {},
                "reading_order": 0,
            }
        ],
        "tables": [],
    }
    resp = _FakeResponse(200, golden)
    fake = _FakeAsyncClient(post_resp=resp)

    with patch("httpx.AsyncClient", return_value=fake):
        data = await client.layout_ocr_pdf_or_image(file_bytes=b"%PDF-1.4\n", filename="t.pdf", max_pages=2)
    assert data["blocks"][0]["text"] == "试"


@pytest.mark.asyncio
async def test_layout_ocr_post_4xx_raises_api_error() -> None:
    cfg = InspectionExtractV0Config(layout_ocr_endpoint="http://127.0.0.1:9")
    client = LayoutOcrClient(cfg)
    mock_resp = MagicMock()
    mock_resp.status_code = 413
    mock_resp.text = "too large"
    mock_resp.json.side_effect = ValueError("not json")

    class _PostClient(_FakeAsyncClient):
        async def post(self, url: str, **kwargs: object) -> MagicMock:
            return mock_resp

    with patch("httpx.AsyncClient", return_value=_PostClient()):
        with pytest.raises(LayoutOcrApiError):
            await client.layout_ocr_pdf_or_image(file_bytes=b"x", filename="a.pdf", max_pages=1)


@pytest.mark.asyncio
async def test_layout_ocr_upload_too_large() -> None:
    cfg = InspectionExtractV0Config(layout_ocr_endpoint="http://x", layout_ocr_max_upload_mb=1)
    client = LayoutOcrClient(cfg)
    big = b"x" * (2 * 1024 * 1024)
    with pytest.raises(LayoutOcrApiError):
        await client.layout_ocr_pdf_or_image(file_bytes=big, filename="a.pdf", max_pages=1)
