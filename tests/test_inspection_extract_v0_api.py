from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("fastapi")

from app.api import inspection_extract_v0 as api
from app.models.inspection_extract import DetectionType, InspectionRecord, InspectionSummary, ReplaceFlag
from app.models.inspection_extract_v0 import (
    InspectionExtractV0Request,
    InspectionExtractV0Response,
    InspectionExtractV0StageLatencyMs,
    InspectionExtractV0Trace,
)


def test_inspection_extract_v0_run_calls_service() -> None:
    req = InspectionExtractV0Request(
        user_id="inspect_user_v0",
        session_id="inspect_session_v0",
        content="demo",
        source_type="text",
    )
    fake = InspectionExtractV0Response(
        ok=True,
        records=[
            InspectionRecord(
                检测位置="左墙A01",
                行号="1",
                管号="10",
                壁厚=5.0,
                检测类型=DetectionType.MEASUREMENT,
                是否换管=ReplaceFlag.NO,
            )
        ],
        summary=InspectionSummary(total=1, defect_count=0, replace_count=0, warnings=[]),
        trace=InspectionExtractV0Trace(
            parse_route="irt_text_fallback",
            llm_model="default",
            prompt_version="inspection_extract_v0:v1",
            parse_latency_ms=1,
            llm_latency_ms=2,
            stage_latency_ms=InspectionExtractV0StageLatencyMs(preprocess=1, llm=2),
        ),
    )
    with patch.object(api.service, "extract_from_document", new=AsyncMock(return_value=fake)) as mocked:
        out = asyncio.run(api.run_inspection_extract_v0(req))
    assert out.ok is True
    assert out.summary.total == 1
    mocked.assert_awaited_once()


def test_inspection_extract_v0_async_submit_calls_service() -> None:
    from app.models.inspection_extract_v0 import InspectionExtractV0AsyncSubmitResponse

    req = InspectionExtractV0Request(
        user_id="inspect_user_v0",
        session_id="inspect_session_v0",
        content="demo",
        source_type="text",
    )
    fake = InspectionExtractV0AsyncSubmitResponse(ok=True, job_id="abc", job_status_path="/inspection-extract-v0/jobs/abc")
    with patch.object(api.service, "submit_async_job", return_value=fake) as mocked:
        out = asyncio.run(api.run_inspection_extract_v0_async(req))
    assert out.job_id == "abc"
    mocked.assert_called_once_with(req)
