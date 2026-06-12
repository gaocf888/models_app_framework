"""统计时间窗展示解析（与 NL2SQL 问句时间意图对齐）。"""

from __future__ import annotations

import unittest
from datetime import datetime

from app.nl2sql.time_intent_display import resolve_statistical_time_range_display


class TestTimeIntentDisplay(unittest.TestCase):
    def test_yesterday_display_range(self):
        ref = datetime(2026, 6, 2, 15, 30, 0)
        out = resolve_statistical_time_range_display(
            "请分析1号机组昨天的超温情况",
            now=ref,
        )
        self.assertEqual(("2026-06-01 00:00:00", "2026-06-01 23:59:59"), out)

    def test_today_display_range(self):
        ref = datetime(2026, 6, 2, 8, 0, 0)
        out = resolve_statistical_time_range_display("今天超温分析", now=ref)
        self.assertEqual(("2026-06-02 00:00:00", "2026-06-02 23:59:59"), out)

    def test_this_week_display_range(self):
        ref = datetime(2026, 6, 4, 12, 0, 0)  # Wednesday
        out = resolve_statistical_time_range_display("本周超温情况", now=ref)
        self.assertEqual(("2026-06-01 00:00:00", "2026-06-07 23:59:59"), out)

    def test_unrecognized_returns_none(self):
        self.assertIsNone(resolve_statistical_time_range_display("分析1号机组超温"))


if __name__ == "__main__":
    unittest.main()
