"""
PaddleOCR 版面+OCR HTTP 服务（检修 V0 专用侧车）。

提供 OpenAPI 文档、健康检查与 /v1/layout-ocr 推理；错误一律返回结构化 JSON。
"""

from __future__ import annotations

import io
import logging
import os
import time
from typing import Any

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pdf2image import convert_from_bytes
from PIL import Image

ENGINE_NAME = "paddleocr-layout-api"
ENGINE_SEMVER = os.getenv("PADDLE_LAYOUT_ENGINE_SEMVER", "0.1.0")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("paddle_layout_api")

_ocr_singleton: Any = None


def _get_ocr() -> Any:
    global _ocr_singleton
    if _ocr_singleton is None:
        from paddleocr import PaddleOCR

        logger.info("initializing PaddleOCR (lazy, first request may be slow)")
        _ocr_singleton = PaddleOCR(
            use_angle_cls=True,
            lang="ch",
            show_log=False,
            use_gpu=os.getenv("PADDLE_LAYOUT_USE_GPU", "0").lower() in ("1", "true", "yes"),
        )
    return _ocr_singleton


def _bbox_from_quad(quad: list[list[float]]) -> dict[str, float]:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return {"x1": float(min(xs)), "y1": float(min(ys)), "x2": float(max(xs)), "y2": float(max(ys))}


def _ocr_image_to_blocks(img: Image.Image, page_no: int) -> tuple[list[dict[str, Any]], int, int]:
    ocr = _get_ocr()
    arr = np.array(img.convert("RGB"))
    t0 = time.perf_counter()
    raw = ocr.ocr(arr, cls=True)
    dt_ms = int((time.perf_counter() - t0) * 1000)
    w, h = img.size
    blocks: list[dict[str, Any]] = []
    if not raw or raw[0] is None:
        return blocks, dt_ms, w, h
    line_idx = 0
    for line in raw[0]:
        if not line or len(line) < 2:
            continue
        quad, txt_conf = line[0], line[1]
        text = str(txt_conf[0] if isinstance(txt_conf, (list, tuple)) else txt_conf)
        conf = float(txt_conf[1]) if isinstance(txt_conf, (list, tuple)) and len(txt_conf) > 1 else 1.0
        bbox = _bbox_from_quad(quad)
        blocks.append(
            {
                "block_id": f"p{page_no}-L{line_idx}",
                "type": "text",
                "page_no": page_no,
                "text": text,
                "confidence": conf,
                "bbox": bbox,
                "reading_order": line_idx,
            }
        )
        line_idx += 1
    return blocks, dt_ms, w, h


def _build_response_from_images(images: list[Image.Image]) -> dict[str, Any]:
    all_blocks: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    total_ocr_ms = 0
    for i, img in enumerate(images, start=1):
        blocks, dt_ms, w, h = _ocr_image_to_blocks(img, i)
        total_ocr_ms += dt_ms
        pages.append({"page_no": i, "width": w, "height": h, "ocr_latency_ms": dt_ms})
        all_blocks.extend(blocks)
    return {
        "engine_version": f"{ENGINE_NAME}/{ENGINE_SEMVER}",
        "ocr_engine": "paddleocr",
        "layout_engine": "paddleocr-det-rec",
        "pages": pages,
        "blocks": all_blocks,
        "tables": [],
        "metrics": {"ocr_total_ms": total_ocr_ms, "page_count": len(pages)},
    }


app = FastAPI(
    title="Paddle Layout + OCR API",
    version=ENGINE_SEMVER,
    description="检修报告 V0：PaddleOCR 文本检测+识别；表格结构化后续可接入 PP-Structure 管线。",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "engine": ENGINE_NAME, "version": ENGINE_SEMVER}


@app.post("/v1/layout-ocr")
async def layout_ocr(
    file: UploadFile = File(..., description="PDF 或 PNG/JPEG 图像"),
    max_pages: int = Query(5, ge=1, le=50, description="PDF 最大渲染页数"),
) -> dict[str, Any]:
    max_body = int(os.getenv("PADDLE_LAYOUT_MAX_UPLOAD_MB", "32")) * 1024 * 1024
    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail={"error": {"code": "EMPTY_FILE", "message": "empty upload"}})
    if len(body) > max_body:
        raise HTTPException(
            status_code=413,
            detail={"error": {"code": "PAYLOAD_TOO_LARGE", "message": f"body>{max_body} bytes"}},
        )
    name = (file.filename or "").lower()
    try:
        if name.endswith(".pdf") or body[:4] == b"%PDF":
            images = convert_from_bytes(body, first_page=1, last_page=max_pages, fmt="png")
            if not images:
                raise ValueError("pdf_render_empty")
        else:
            images = [Image.open(io.BytesIO(body))]
        payload = _build_response_from_images(images)
        return payload
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("layout_ocr failed")
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "LAYOUT_OCR_FAILED", "message": str(exc)[:2000]}},
        )


@app.exception_handler(Exception)
async def _unhandled(_request, exc: Exception) -> JSONResponse:  # noqa: ANN001
    logger.exception("unhandled")
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL", "message": str(exc)[:2000]}},
    )
