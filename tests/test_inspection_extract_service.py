from __future__ import annotations

import json
import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.models.inspection_extract import InspectionExtractRequest
from app.services.inspection_extract_service import InspectionExtractService


class _FakeLLM:
    def __init__(self) -> None:
        self._idx = 0
        self._responses = [
            json.dumps(
                {
                    "records": [
                        {"检测位置": "右墙B02", "行号": "1", "管号": "12", "壁厚": 4.73, "evidence": "表格行"}
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "records": [
                        {
                            "检测位置": "右墙B02",
                            "行号": "1",
                            "管号": "12",
                            "壁厚": 4.73,
                            "检测类型": "缺陷",
                            "缺陷类型": "表面吹损",
                            "是否换管": "是",
                            "evidence": "表格行",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "records": [
                        {
                            "检测位置": "右墙B02",
                            "行号": "1",
                            "管号": "12",
                            "壁厚": 4.73,
                            "检测类型": "缺陷",
                            "缺陷类型": "表面吹损",
                            "是否换管": "是",
                            "evidence": "表格行",
                            "warnings": [],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ]

    async def generate(self, model: str, prompt: str, **kwargs: object) -> str:  # noqa: ARG002
        out = self._responses[self._idx]
        self._idx = min(self._idx + 1, len(self._responses) - 1)
        return out


def test_extract_from_document_returns_structured_rows() -> None:
    svc = InspectionExtractService(llm_client=_FakeLLM())  # type: ignore[arg-type]
    req = InspectionExtractRequest(
        user_id="inspect_user_1",
        session_id="inspect_sess_1",
        content="检修报告示例文本",
        source_type="text",
        strict=False,
    )
    out = asyncio.run(svc.extract_from_document(req))
    assert out.ok is True
    assert out.summary.total == 1
    assert out.summary.defect_count == 1
    assert out.records[0].location == "右墙B02"
    assert out.records[0].tube_no == "12"
    assert out.trace.parse_route == "text"


def test_parse_document_supports_docx_url() -> None:
    svc = InspectionExtractService(llm_client=_FakeLLM())  # type: ignore[arg-type]
    fd, tmp = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    Path(tmp).write_bytes(b"docx")
    with patch.object(svc, "_download_to_temp_file", return_value=Path(tmp)) as d_mock:
        with patch.object(svc._parser, "parse", return_value="docx parsed") as p_mock:  # noqa: SLF001
            parsed, route = svc._parse_document(  # noqa: SLF001
                InspectionExtractRequest(
                    user_id="inspect_user_2",
                    session_id="inspect_sess_2",
                    content="http://minio.local/presigned.docx",
                    source_type="docx",
                )
            )
    assert parsed == "docx parsed"
    assert route == "docx"
    d_mock.assert_called_once()
    p_mock.assert_called_once()


def test_upload_file_returns_presigned_url() -> None:
    class _FakeMinio:
        def put_object(self, **kwargs):  # noqa: ANN003
            return None

        def presigned_get_object(self, **kwargs):  # noqa: ANN003
            return "http://minio/presigned-object"

    svc = InspectionExtractService(llm_client=_FakeLLM())  # type: ignore[arg-type]
    svc._minio = _FakeMinio()  # noqa: SLF001
    out = asyncio.run(svc.upload_file(file_name="report.docx", content=b"demo"))
    assert out.ok is True
    assert out.source_type == "docx"
    assert out.url.startswith("http://minio/")

