import unittest

from app.rag.text_quality import looks_like_binary_text


class TestTextQuality(unittest.TestCase):
    def test_detects_pdf_header_in_text(self) -> None:
        self.assertTrue(looks_like_binary_text("%PDF-1.4\n" + "x" * 100))

    def test_normal_chinese_text_not_binary(self) -> None:
        self.assertFalse(looks_like_binary_text("地面沉降的核心检测技术与方法有哪些。" * 5))

    def test_high_non_printable_ratio(self) -> None:
        garbage = "\x00\x01\x02\x03" * 200 + "abc"
        self.assertTrue(looks_like_binary_text(garbage))


if __name__ == "__main__":
    unittest.main()
