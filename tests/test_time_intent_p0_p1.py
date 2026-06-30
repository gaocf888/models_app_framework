"""P0/P1 时间窗解析：季度、半年、精确日、无年月份、近 N 年、前年、大前天。"""

from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch

from app.nl2sql.time_intent_display import (
    extract_time_window_from_question,
    extract_time_window_tag,
    resolve_statistical_time_range_display,
)


class TestTimeIntentP0P1(unittest.TestCase):
    def test_year_quarter_not_whole_year(self) -> None:
        win = extract_time_window_from_question("请分析2025年第一季度超温统计")
        assert win is not None
        start, end, tag = win
        self.assertEqual("quarter_2025_1", tag)
        self.assertIn("2025-01-01", start)
        self.assertIn("2025-04-01", end)
        self.assertNotEqual("year_2025", tag)

    def test_q1_alias(self) -> None:
        win = extract_time_window_from_question("2025年q1运行情况")
        assert win is not None
        self.assertEqual("quarter_2025_1", win[2])

    def test_this_and_last_quarter(self) -> None:
        tq = extract_time_window_tag("本季度超温汇总")
        lq = extract_time_window_tag("上季度超温汇总")
        self.assertEqual("this_quarter", tq)
        self.assertEqual("last_quarter", lq)

    def test_ordinal_quarter_current_year(self) -> None:
        win = extract_time_window_from_question("第三季度数据统计")
        assert win is not None
        self.assertEqual("quarter_cur_3", win[2])
        self.assertIn("-07-01", win[0])

    def test_three_days_ago_before_day_before_yesterday(self) -> None:
        win = extract_time_window_from_question("大前天超温情况")
        assert win is not None
        self.assertEqual("three_days_ago", win[2])
        self.assertIn("INTERVAL 3 DAY", win[0])

    def test_half_year_calendar(self) -> None:
        h1 = extract_time_window_from_question("上半年检修统计")
        h2 = extract_time_window_from_question("下半年检修统计")
        assert h1 is not None and h2 is not None
        self.assertEqual("half_first", h1[2])
        self.assertEqual("half_second", h2[2])
        self.assertIn("%Y-01-01", h1[0])
        self.assertIn("%Y-07-01", h2[0])

    def test_exact_day_iso_and_chinese(self) -> None:
        iso = extract_time_window_from_question("2026-05-19超温记录")
        zh = extract_time_window_from_question("2026年5月19日超温记录")
        assert iso is not None and zh is not None
        self.assertEqual("day_2026_05_19", iso[2])
        self.assertEqual("day_2026_05_19", zh[2])
        self.assertIn("2026-05-19", iso[0])
        self.assertIn("2026-05-20", iso[1])

    def test_month_without_year(self) -> None:
        win = extract_time_window_from_question("5月份超温统计")
        assert win is not None
        self.assertEqual("month_cur_05", win[2])
        self.assertIn("'-05-01'", win[0])

    def test_recent_n_years(self) -> None:
        win = extract_time_window_from_question("近三年超温趋势")
        assert win is not None
        self.assertEqual("recent_3_years", win[2])
        self.assertIn("INTERVAL 3 YEAR", win[0])

    def test_year_before_last(self) -> None:
        win = extract_time_window_from_question("前年超温对比")
        assert win is not None
        self.assertEqual("year_before_last", win[2])
        self.assertIn("INTERVAL 2 YEAR", win[0])

    def test_near_half_year_still_rolling(self) -> None:
        """近半年仍为滚动窗，不与日历上半年混淆。"""
        win = extract_time_window_from_question("近半年超温")
        assert win is not None
        self.assertEqual("recent_6_months", win[2])

    def test_display_this_quarter(self) -> None:
        ref = datetime(2026, 5, 19, 10, 0, 0)
        out = resolve_statistical_time_range_display("本季度超温统计", now=ref)
        self.assertEqual(("2026-04-01 00:00:00", "2026-06-30 23:59:59"), out)

    def test_display_year_quarter(self) -> None:
        ref = datetime(2026, 5, 19, 10, 0, 0)
        out = resolve_statistical_time_range_display("2025年第一季度统计", now=ref)
        self.assertEqual(("2025-01-01 00:00:00", "2025-03-31 23:59:59"), out)


class TestTimeIntentPartialMonthDay(unittest.TestCase):
    """无年/无月日：当年当月；左闭右开单日窗。"""

    _REF_DATE = date(2026, 6, 2)

    def _patch_today(self):
        return patch(
            "app.nl2sql.time_intent_display.date",
            wraps=date,
        )

    def test_month_day_chinese(self) -> None:
        with self._patch_today() as mock_date:
            mock_date.today.return_value = self._REF_DATE
            win = extract_time_window_from_question("6月25日1号锅炉超温")
        assert win is not None
        self.assertEqual("day_cur_06_25", win[2])
        self.assertIn("YEAR(CURDATE())", win[0])
        self.assertIn("-06-25", win[0])

    def test_month_day_formats(self) -> None:
        cases = [
            ("6/25超温", "day_cur_06_25"),
            ("06/25超温", "day_cur_06_25"),
            ("06-25超温", "day_cur_06_25"),
            ("6-25超温", "day_cur_06_25"),
            ("6.25超温", "day_cur_06_25"),
            ("6月25号超温", "day_cur_06_25"),
        ]
        with self._patch_today() as mock_date:
            mock_date.today.return_value = self._REF_DATE
            for q, tag in cases:
                win = extract_time_window_from_question(q)
                assert win is not None, q
                self.assertEqual(tag, win[2], q)

    def test_day_of_month_only(self) -> None:
        with self._patch_today() as mock_date:
            mock_date.today.return_value = self._REF_DATE
            win = extract_time_window_from_question("请分析1号锅炉25日超温")
        assert win is not None
        self.assertEqual("day_cur_m_25", win[2])
        self.assertIn("DATE_FORMAT(CURDATE(), '%Y-%m-')", win[0])

    def test_day_only_with_hao(self) -> None:
        with self._patch_today() as mock_date:
            mock_date.today.return_value = self._REF_DATE
            tag = extract_time_window_tag("25号超温统计")
        self.assertEqual("day_cur_m_25", tag)

    def test_display_month_day_and_day_only(self) -> None:
        ref = datetime(2026, 6, 2, 10, 0, 0)
        md = resolve_statistical_time_range_display("6月25日超温", now=ref)
        self.assertEqual(("2026-06-25 00:00:00", "2026-06-25 23:59:59"), md)
        d_only = resolve_statistical_time_range_display("25日超温", now=ref)
        self.assertEqual(("2026-06-25 00:00:00", "2026-06-25 23:59:59"), d_only)

    def test_full_ymd_still_wins(self) -> None:
        win = extract_time_window_from_question("2026年6月25日超温")
        assert win is not None
        self.assertEqual("day_2026_06_25", win[2])

    def test_false_positive_guards(self) -> None:
        with self._patch_today() as mock_date:
            mock_date.today.return_value = self._REF_DATE
            self.assertIsNone(extract_time_window_tag("1号锅炉第25排超温"))
            self.assertIsNone(extract_time_window_tag("25号锅炉超温"))
            self.assertEqual("recent_25_days", extract_time_window_tag("近25天超温"))
            self.assertEqual("month_cur_06", extract_time_window_tag("6月份超温"))


if __name__ == "__main__":
    unittest.main()
