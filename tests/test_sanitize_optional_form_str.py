"""Form 占位符清洗单测。"""

from __future__ import annotations

import unittest

from app.rag.original_docs import sanitize_optional_form_str


class TestSanitizeOptionalFormStr(unittest.TestCase):
    def test_none_and_blank(self) -> None:
        self.assertIsNone(sanitize_optional_form_str(None))
        self.assertIsNone(sanitize_optional_form_str(""))
        self.assertIsNone(sanitize_optional_form_str("   "))

    def test_swagger_placeholders(self) -> None:
        for raw in ("string", "String", "null", "None", "undefined", "str"):
            self.assertIsNone(sanitize_optional_form_str(raw), msg=raw)

    def test_real_values_kept(self) -> None:
        self.assertEqual("堤防规范", sanitize_optional_form_str(" 堤防规范 "))
        self.assertEqual("09-GB50286", sanitize_optional_form_str("09-GB50286"))


if __name__ == "__main__":
    unittest.main()
