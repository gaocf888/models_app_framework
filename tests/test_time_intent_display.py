"""统计时间窗展示解析（与 NL2SQL 问句时间意图对齐）。"""

from __future__ import annotations

import unittest
from datetime import datetime

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.time_intent_display import (
    DEFAULT_TIME_WINDOW_TAG,
    default_time_window_sql_fallback,
    resolve_statistical_time_range_display,
)


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

    def test_default_yesterday_when_no_time(self):
        ref = datetime(2026, 6, 2, 15, 30, 0)
        out = resolve_statistical_time_range_display("分析1号机组超温", now=ref)
        self.assertEqual(("2026-06-01 00:00:00", "2026-06-01 23:59:59"), out)

    def test_default_time_window_sql_fallback(self):
        start, end, tag = default_time_window_sql_fallback()
        self.assertEqual("DATE_SUB(CURDATE(), INTERVAL 1 DAY)", start)
        self.assertEqual("CURDATE()", end)
        self.assertEqual(DEFAULT_TIME_WINDOW_TAG, tag)

    def test_chain_applies_default_yesterday_when_no_time(self):
        chain = NL2SQLChain.__new__(NL2SQLChain)
        meta: dict = {}
        win = chain._resolve_time_window_for_rewrite(
            question="在用户指定时间窗内查询超温明细",
            time_intent_source="分析1号机组超温",
            parsed_intent={},
            rewrite_meta=meta,
        )
        assert win is not None
        self.assertEqual(default_time_window_sql_fallback(), win)
        self.assertIn("default_yesterday_fallback", meta.get("time_rewrite_warnings", []))


if __name__ == "__main__":
    unittest.main()
