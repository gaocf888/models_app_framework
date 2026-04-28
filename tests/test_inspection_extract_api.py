from __future__ import annotations

import asyncio
import io
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")

from fastapi import UploadFile

from app.api import inspection_extract as api
from app.models.inspection_extract import (
    DetectionType,
    InspectionExtractRequest,
    InspectionExtractResponse,
    InspectionExtractTrace,
    InspectionUploadResponse,
    InspectionRecord,
    InspectionSummary,
    ReplaceFlag,
)


def test_inspection_extract_route_calls_service() -> None:
    req = InspectionExtractRequest(
        user_id="inspect_user_api",
        session_id="inspect_session_api",
        content="demo",
        source_type="text",
    )
    fake = InspectionExtractResponse(
        ok=True,
        records=[
            InspectionRecord(
                检测位置="右墙B02",
                行号="1",
                管号="12",
                壁厚=4.8,
                检测类型=DetectionType.MEASUREMENT,
                是否换管=ReplaceFlag.NO,
            )
        ],
        summary=InspectionSummary(total=1, defect_count=0, replace_count=0, warnings=[]),
        trace=InspectionExtractTrace(
            parse_route="text",
            llm_model="default",
            prompt_version="inspection_extract:v1",
            parse_latency_ms=1,
            llm_latency_ms=2,
        ),
    )
    with patch.object(api.service, "extract_from_document", new=AsyncMock(return_value=fake)) as mocked:
        out = asyncio.run(api.run_inspection_extract(req))
    assert out.ok is True
    assert out.summary.total == 1
    mocked.assert_awaited_once()


def test_inspection_upload_route_calls_service() -> None:
    fake = InspectionUploadResponse(
        ok=True,
        file_name="demo.docx",
        object_name="inspection_extract/abc_demo.docx",
        source_type="docx",
        url="http://minio/presigned",
        bucket="chatbot-images",
    )
    up = UploadFile(filename="demo.docx", file=io.BytesIO(b"abc"), headers=None)
    with patch.object(api.service, "upload_file", new=AsyncMock(return_value=fake)) as mocked:
        out = asyncio.run(api.upload_inspection_report(up))
    assert out.ok is True
    assert out.source_type == "docx"
    mocked.assert_awaited_once()

