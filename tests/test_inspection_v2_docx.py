from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from app.inspection_v2.docx_rich_text import normalize_shading_fill, serialize_docx_for_inspection_v2
from app.models.inspection_extract import InspectionExtractRequest
from app.services.inspection_extract_service import InspectionExtractService


def _set_cell_shading(cell, fill_hex: str) -> None:
    tc = cell._tc
    tc_pr = tc.find(qn("w:tcPr"))
    if tc_pr is None:
        tc_pr = OxmlElement("w:tcPr")
        tc.insert(0, tc_pr)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), fill_hex)
    old = tc_pr.find(qn("w:shd"))
    if old is not None:
        tc_pr.remove(old)
    tc_pr.append(shd)


def test_normalize_shading_fill_argb_and_hash() -> None:
    assert normalize_shading_fill("FFFF0000") == "FF0000"
    assert normalize_shading_fill("#C00000") == "C00000"


def test_serialize_docx_marks_shading_candidate() -> None:
    doc = Document()
    doc.add_paragraph("锅炉检修")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "右墙"
    table.cell(0, 1).text = "4.2"
    _set_cell_shading(table.cell(0, 1), "FF0000")

    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    doc.save(path)
    try:
        out = serialize_docx_for_inspection_v2(path, candidate_fills={"FF0000"})
        assert "锅炉检修" in out
        assert "[DOCX_V2_TABLE" in out
        assert "超标候选" in out
        assert "底纹=FF0000" in out
        assert "c1=" in out
    finally:
        Path(path).unlink(missing_ok=True)


def test_parse_document_v2_returns_docx_v2_route() -> None:
    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "x"
    fd, path = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    doc.save(path)
    try:
        svc = InspectionExtractService()  # type: ignore[call-arg]
        parsed, route = svc._parse_document_v2(  # noqa: SLF001
            InspectionExtractRequest(
                user_id="u1",
                session_id="s1",
                content=str(path),
                source_type="docx",
            )
        )
        assert route == "docx_v2"
        assert "[DOCX_V2_TABLE" in parsed
    finally:
        Path(path).unlink(missing_ok=True)


def test_parse_document_v2_pdf_delegates_to_v1() -> None:
    svc = InspectionExtractService()  # type: ignore[call-arg]
    with patch.object(svc, "_parse_document", return_value=("pdf-body", "pdf_text")) as m:
        out, route = svc._parse_document_v2(  # noqa: SLF001
            InspectionExtractRequest(
                user_id="u1",
                session_id="s1",
                content="/tmp/x.pdf",
                source_type="pdf",
            )
        )
    assert (out, route) == ("pdf-body", "pdf_text")
    m.assert_called_once()
