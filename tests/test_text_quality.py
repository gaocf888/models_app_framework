import unittest

from app.rag.document_pipeline.enrichers import chunk_hash
from app.rag.document_pipeline.cleaners import TextCleaner
from app.rag.text_quality import looks_like_binary_text, strip_utf16_surrogates


class TestTextQuality(unittest.TestCase):
    def test_detects_pdf_header_in_text(self) -> None:
        self.assertTrue(looks_like_binary_text("%PDF-1.4\n" + "x" * 100))

    def test_normal_chinese_text_not_binary(self) -> None:
        self.assertFalse(looks_like_binary_text("地面沉降的核心检测技术与方法有哪些。" * 5))

    def test_high_non_printable_ratio(self) -> None:
        garbage = "\x00\x01\x02\x03" * 200 + "abc"
        self.assertTrue(looks_like_binary_text(garbage))

    def test_strip_utf16_surrogates(self) -> None:
        raw = "沉降监测" + "\ud800\udc00" + "技术要求"
        out = strip_utf16_surrogates(raw)
        self.assertEqual("沉降监测技术要求", out)
        out.encode("utf-8")

    def test_chunk_hash_with_surrogates(self) -> None:
        dirty = "地面沉降" + "\ud800\udc00" + "规范"
        clean = "地面沉降规范"
        self.assertEqual(chunk_hash(dirty), chunk_hash(clean))
        self.assertEqual(64, len(chunk_hash(dirty)))

    def test_cleaner_strips_surrogates(self) -> None:
        raw = "正文" + "\udc00" + "继续"
        out = TextCleaner(profile="light").clean(raw)
        self.assertEqual("正文继续", out)
        out.encode("utf-8")


if __name__ == "__main__":
    unittest.main()
