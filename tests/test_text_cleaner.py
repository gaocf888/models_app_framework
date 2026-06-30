import unittest

from app.rag.document_pipeline.cleaners import TextCleaner


class TestTextCleaner(unittest.TestCase):
    def test_remove_repeated_header_footer_by_page(self):
        raw = (
            "企业机密文档\n正文第一页内容\n第 1 页\f"
            "企业机密文档\n正文第二页内容\n第 2 页\f"
            "企业机密文档\n正文第三页内容\n第 3 页"
        )
        cleaner = TextCleaner(profile="normal", remove_header_footer=True, min_repeated_line_pages=2)
        out = cleaner.clean(raw)
        self.assertIn("正文第一页内容", out)
        self.assertIn("正文第二页内容", out)
        self.assertNotIn("企业机密文档", out)

    def test_merge_duplicate_paragraphs(self):
        raw = "重复段落A。\n\n重复段落A。\n\n重复段落B。"
        cleaner = TextCleaner(profile="normal", merge_duplicate_paragraphs=True)
        out = cleaner.clean(raw)
        self.assertEqual("重复段落A。\n\n重复段落B。", out)

    def test_fix_encoding_noise(self):
        raw = "This is â€œquotedâ€ text with Â noise and bad char\ufffd."
        cleaner = TextCleaner(profile="light", fix_encoding_noise=True)
        out = cleaner.clean(raw)
        self.assertIn('"quoted"', out)
        self.assertNotIn("Â", out)
        self.assertNotIn("\ufffd", out)

    def test_strip_embedded_html_table(self):
        raw = "前言<table><tr><td>单元格A</td><td>单元格B</td></tr></table>后文"
        cleaner = TextCleaner(profile="normal", strip_html=True)
        out = cleaner.clean(raw)
        self.assertIn("前言", out)
        self.assertIn("单元格A", out)
        self.assertIn("单元格B", out)
        self.assertIn("后文", out)
        self.assertNotIn("<table>", out)
        self.assertNotIn("<td>", out)

    def test_strip_embedded_html_div(self):
        raw = '<div class="note">说明文字</div>正文'
        cleaner = TextCleaner(profile="normal", strip_html=True)
        out = cleaner.clean(raw)
        self.assertIn("说明文字", out)
        self.assertIn("正文", out)
        self.assertNotIn("<div", out)

    def test_strip_html_skips_numeric_comparison(self):
        raw = "炉温应低于500℃，当温度<500℃时可停机。"
        cleaner = TextCleaner(profile="normal", strip_html=True)
        out = cleaner.clean(raw)
        self.assertIn("温度<500℃", out)

    def test_strip_html_disabled_keeps_markup(self):
        raw = "<p>保留标签</p>"
        cleaner = TextCleaner(profile="normal", strip_html=False)
        out = cleaner.clean(raw)
        self.assertIn("<p>保留标签</p>", out)


if __name__ == "__main__":
    unittest.main()
