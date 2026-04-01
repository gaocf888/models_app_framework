from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.rag.models import DocumentSource
from app.rag.pdf_text_analysis import is_likely_scanned_pdf


def test_is_likely_scanned_pdf_low_text(tmp_path: Path) -> None:
    class FakePage:
        def __init__(self, text: str = "") -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class FakeReader:
        def __init__(self, _path: str) -> None:
            self.pages = [FakePage(""), FakePage("  ")]

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"x")
    scanned, stats = is_likely_scanned_pdf(pdf, max_avg_chars_for_text_pdf=40.0, pdf_reader_cls=FakeReader)
    assert scanned is True
    assert stats.page_count == 2


def test_is_likely_scanned_pdf_rich_text(tmp_path: Path) -> None:
    class FakePage:
        def extract_text(self) -> str:
            return "word " * 200

    class FakeReader:
        def __init__(self, _path: str) -> None:
            self.pages = [FakePage()]

    pdf = tmp_path / "b.pdf"
    pdf.write_bytes(b"x")
    scanned, stats = is_likely_scanned_pdf(pdf, max_avg_chars_for_text_pdf=40.0, pdf_reader_cls=FakeReader)
    assert scanned is False
    assert stats.avg_chars_per_sampled_page > 40


def test_prepare_pdf_skips_mineru_when_text_layer(tmp_path: Path) -> None:
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(b"x")

    from app.rag import mineru_ingest as mi

    m_cfg = MagicMock()
    m_cfg.mineru.enabled = True
    m_cfg.mineru.pdf_scanned_max_avg_chars = 40.0
    fake_stats = MagicMock(
        page_count=3,
        sampled_pages=1,
        total_chars_sampled=900,
        avg_chars_per_sampled_page=900.0,
    )
    with patch.object(mi, "get_app_config", return_value=m_cfg):
        with patch.object(mi, "is_likely_scanned_pdf", return_value=(False, fake_stats)):
            doc = DocumentSource(
                dataset_id="d",
                doc_name="n",
                namespace=None,
                content=str(pdf),
                source_type="pdf",
            )
            out, wall = mi.prepare_pdf_document_for_pipeline(doc)
    assert wall is None
    assert out.source_type == "pdf"
    assert out is doc
