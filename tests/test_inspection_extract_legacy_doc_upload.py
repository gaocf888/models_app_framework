from __future__ import annotations

import asyncio
import io

import pytest

pytest.importorskip("fastapi")

from fastapi import HTTPException, UploadFile

from app.api import inspection_extract as api
from app.inspection_extract.legacy_doc_guard import (
    LEGACY_DOC_UPLOAD_MESSAGE,
    assert_upload_not_legacy_doc,
    is_legacy_word_doc,
)

_OLE_HEAD = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 8


def test_is_legacy_word_doc_by_extension() -> None:
    assert is_legacy_word_doc(file_name="report.doc", content=b"any") is True


def test_is_not_legacy_docx_zip() -> None:
    assert is_legacy_word_doc(file_name="report.docx", content=b"PK\x03\x04") is False


def test_is_legacy_ole_misnamed_docx() -> None:
    assert is_legacy_word_doc(file_name="report.docx", content=_OLE_HEAD) is True


def test_upload_route_rejects_doc() -> None:
    up = UploadFile(filename="report.doc", file=io.BytesIO(_OLE_HEAD), headers=None)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(api.upload_inspection_report(up))
    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert detail["error"]["code"] == "LEGACY_DOC_NOT_SUPPORTED"
    assert "docx" in detail["error"]["message"]


def test_assert_upload_message() -> None:
    with pytest.raises(Exception) as exc_info:
        assert_upload_not_legacy_doc(file_name="a.doc", content=b"x")
    assert LEGACY_DOC_UPLOAD_MESSAGE in str(exc_info.value)
