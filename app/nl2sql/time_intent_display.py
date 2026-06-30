"""
用户问句时间意图：与 NL2SQL 改写规则对齐的 tag 解析 + 报告展示边界。

``extract_time_window_from_question`` 供 ``NL2SQLChain`` 与 synthesis 共用，避免重复逻辑。
日历日窗展示结束时间为当日 23:59:59（SQL 取数仍为左闭右开区间）。
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time, timedelta

DAY_WINDOW_TAGS = frozenset(
    {"today", "yesterday", "day_before_yesterday", "three_days_ago"}
)

# 问句未解析到任何时间表达时，全场景默认统计口径（展示与 NL2SQL 改写共用 tag）
DEFAULT_TIME_WINDOW_TAG = "yesterday_fallback"

_QUARTER_CN_MAP = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "1": 1,
    "2": 2,
    "3": 3,
    "4": 4,
}

_ORDINAL_QUARTER_RE = re.compile(
    r"第\s*([一二三四1-4])\s*季度|第\s*([一二三四1-4])\s*季"
)
_YEAR_QUARTER_RE = re.compile(
    r"(20\d{2})\s*年\s*(?:第\s*([一二三四1-4])\s*季度|第\s*([一二三四1-4])\s*季|q\s*([1-4]))"
)
_YEAR_Q_RE = re.compile(r"(20\d{2})\s*q\s*([1-4])\b")
_EXACT_DAY_YMD_RE = re.compile(
    r"(20\d{2})年(0?[1-9]|1[0-2])月(0?[1-9]|[12]\d|3[01])日"
)
_EXACT_DAY_ISO_RE = re.compile(
    r"(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])(?!\d)"
)
_MONTH_ONLY_RE = re.compile(r"(?<![0-9年\-])(0?[1-9]|1[0-2])月(?:份)?(?![0-9日\-])")
# 无四位年份的月日：6月25日、6/25、06-25、6.25（须在整月规则之前匹配）
_MONTH_DAY_CN_RE = re.compile(
    r"(?<![0-9年])(0?[1-9]|1[0-2])月(0?[1-9]|[12]\d|3[01])[日号]"
)
_MONTH_DAY_SLASH_RE = re.compile(
    r"(?<![0-9/])(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?![0-9/])"
)
_MONTH_DAY_DASH_RE = re.compile(
    r"(?<![0-9\-])(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])(?![0-9\-])"
)
_MONTH_DAY_DOT_RE = re.compile(
    r"(?<![0-9.])(0?[1-9]|1[0-2])\.(0?[1-9]|[12]\d|3[01])(?![0-9.])"
)
# 仅日（当年当月）：25日、25号；排除第25排、25号锅炉等
_DAY_ONLY_RE = re.compile(
    r"(?<![第0-9年月/\.\-])"
    r"(0?[1-9]|[12]\d|3[01])"
    r"(?:日|号(?!锅炉|机组|排|根|管))"
)
_PLAN_ANCHOR_LOOKBACK_RE = re.compile(
    r"锚点\s*向前\s*(\d+|[一二两三四五六七八九十百]+)\s*天"
)
_ANCHOR_DATETIME_YMD_HM_RE = re.compile(
    r"(20\d{2})年(0?[1-9]|1[0-2])月(0?[1-9]|[12]\d|3[01])日\s*(\d{1,2})[点时](?:\s*(\d{1,2})分)?"
)
_ANCHOR_DATETIME_ISO_RE = re.compile(
    r"(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])[ T](\d{1,2}):(\d{2})(?::(\d{2}))?"
)


def _parse_cn_or_arabic_int(raw: str) -> int | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        n = int(s)
        return n if n > 0 else None
    zh_map = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if s in zh_map:
        return zh_map[s]
    if s == "十":
        return 10
    if s.startswith("十") and len(s) == 2:
        return 10 + zh_map.get(s[1], 0)
    if "十" in s and len(s) == 2:
        return zh_map.get(s[0], 1) * 10 + zh_map.get(s[1], 0)
    return None


def parse_plan_anchor_lookback_days(plan_question: str) -> int | None:
    """从 plan 问句解析「锚点向前 N 天」中的 N；未命中返回 None。"""
    m = _PLAN_ANCHOR_LOOKBACK_RE.search(plan_question or "")
    if not m:
        return None
    return _parse_cn_or_arabic_int(m.group(1))


def _try_anchor_datetime(q: str) -> tuple[str, str] | None:
    """解析带时刻的事故锚点，返回 (end_expr, tag)。"""
    m = _ANCHOR_DATETIME_YMD_HM_RE.search(q)
    if m:
        y, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hour = int(m.group(4))
        minute = int(m.group(5)) if m.group(5) else 0
        try:
            datetime(y, mon, day, hour, minute, 0)
        except ValueError:
            return None
        end_expr = f"'{y:04d}-{mon:02d}-{day:02d} {hour:02d}:{minute:02d}:00'"
        return end_expr, f"anchor_datetime_{y:04d}{mon:02d}{day:02d}{hour:02d}{minute:02d}"
    m2 = _ANCHOR_DATETIME_ISO_RE.search(q)
    if m2:
        y = int(m2.group(1))
        mon = int(m2.group(2))
        day = int(m2.group(3))
        hour = int(m2.group(4))
        minute = int(m2.group(5))
        sec = int(m2.group(6)) if m2.group(6) else 0
        try:
            datetime(y, mon, day, hour, minute, sec)
        except ValueError:
            return None
        end_expr = f"'{y:04d}-{mon:02d}-{day:02d} {hour:02d}:{minute:02d}:{sec:02d}'"
        return end_expr, f"anchor_datetime_{y:04d}{mon:02d}{day:02d}{hour:02d}{minute:02d}"
    return None


def extract_time_anchor_from_question(question: str) -> tuple[str, str] | None:
    """
    从事故/用户问句解析锚点上界 end_expr（左闭右开区间的右端点）。

    仅识别明确日期/时刻或相对日（今天/昨天/前天等），不把「近 N 天/月」等滚动窗当作锚点。
    """
    q = (question or "").strip().lower()
    if not q:
        return None
    dt = _try_anchor_datetime(q)
    if dt:
        return dt
    exact = _try_exact_day(q)
    if exact:
        _start, end_expr, tag = exact
        return end_expr, f"anchor_{tag}"
    win = extract_time_window_from_question(q)
    if win is None:
        return None
    _start, end_expr, tag = win
    if tag in DAY_WINDOW_TAGS or tag.startswith("day_"):
        return end_expr, f"anchor_{tag}"
    return None


def build_anchor_lookback_time_window(
    anchor_end_expr: str,
    lookback_days: int,
) -> tuple[str, str, str]:
    """plan 触发锚点回溯：``[anchor_end - N 天, anchor_end)``。"""
    n = max(1, int(lookback_days))
    start_expr = f"DATE_SUB({anchor_end_expr}, INTERVAL {n} DAY)"
    return start_expr, anchor_end_expr, f"anchor_lookback_{n}d"


def extract_numeric_window(q: str, unit_keys: tuple[str, ...]) -> int | None:
    pat = re.compile(
        rf"(?:近|最近|过去|recent|last|past)\s*([0-9]{{1,3}})\s*({'|'.join(unit_keys)})",
        re.IGNORECASE,
    )
    m = pat.search(q)
    if m:
        return max(1, int(m.group(1)))
    zh_pat = re.compile(rf"(?:近|最近|过去)\s*([一二两三四五六七八九十百]+)\s*({'|'.join(unit_keys)})")
    m2 = zh_pat.search(q)
    if not m2:
        return None
    zh = m2.group(1)
    zh_map = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
    }
    if zh == "十":
        return 10
    if zh.endswith("十") and len(zh) == 2:
        return zh_map.get(zh[0], 1) * 10
    if "十" in zh and len(zh) == 2:
        return 10 + zh_map.get(zh[1], 0)
    return zh_map.get(zh)


def _quarter_start_month(quarter: int) -> int:
    return (quarter - 1) * 3 + 1


def _quarter_end_ym(year: int, quarter: int) -> tuple[int, int]:
    sm = _quarter_start_month(quarter)
    if quarter == 4:
        return year + 1, 1
    return year, sm + 3


def quarter_bounds_sql(*, year: int | None, quarter: int) -> tuple[str, str, str]:
    """自然年季度 [start, end) 半开区间 SQL 与 tag。"""
    sm = _quarter_start_month(quarter)
    if year is None:
        start = f"DATE(CONCAT(YEAR(CURDATE()), '-{sm:02d}-01'))"
        if quarter == 4:
            end = "DATE_ADD(DATE(CONCAT(YEAR(CURDATE()), '-01-01')), INTERVAL 1 YEAR)"
        else:
            end_m = sm + 3
            end = f"DATE(CONCAT(YEAR(CURDATE()), '-{end_m:02d}-01'))"
        return start, end, f"quarter_cur_{quarter}"
    end_y, end_m = _quarter_end_ym(year, quarter)
    return (
        f"'{year:04d}-{sm:02d}-01 00:00:00'",
        f"'{end_y:04d}-{end_m:02d}-01 00:00:00'",
        f"quarter_{year}_{quarter}",
    )


def this_quarter_bounds_sql() -> tuple[str, str, str]:
    start = (
        "DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-%m-01'), "
        "INTERVAL ((MONTH(CURDATE()) - 1) % 3) MONTH)"
    )
    end = f"DATE_ADD({start}, INTERVAL 3 MONTH)"
    return start, end, "this_quarter"


def last_quarter_bounds_sql() -> tuple[str, str, str]:
    this_start = (
        "DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-%m-01'), "
        "INTERVAL ((MONTH(CURDATE()) - 1) % 3) MONTH)"
    )
    start = f"DATE_SUB({this_start}, INTERVAL 3 MONTH)"
    end = this_start
    return start, end, "last_quarter"


def half_year_bounds_sql(*, which: str) -> tuple[str, str, str]:
    if which == "first":
        return (
            "DATE_FORMAT(CURDATE(), '%Y-01-01')",
            "DATE_FORMAT(CURDATE(), '%Y-07-01')",
            "half_first",
        )
    return (
        "DATE_FORMAT(CURDATE(), '%Y-07-01')",
        "DATE_ADD(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR)",
        "half_second",
    )


def _parse_quarter_token(raw: str | None) -> int | None:
    if not raw:
        return None
    return _QUARTER_CN_MAP.get(raw.strip())


def _try_year_quarter(q: str) -> tuple[str, str, str] | None:
    m = _YEAR_QUARTER_RE.search(q)
    if m:
        year = int(m.group(1))
        qn = _parse_quarter_token(m.group(2) or m.group(3) or m.group(4))
        if qn:
            return quarter_bounds_sql(year=year, quarter=qn)
    m2 = _YEAR_Q_RE.search(q)
    if m2:
        return quarter_bounds_sql(year=int(m2.group(1)), quarter=int(m2.group(2)))
    return None


def _try_ordinal_quarter(q: str) -> tuple[str, str, str] | None:
    m = _ORDINAL_QUARTER_RE.search(q)
    if not m:
        return None
    qn = _parse_quarter_token(m.group(1) or m.group(2))
    if not qn:
        return None
    return quarter_bounds_sql(year=None, quarter=qn)


def _try_exact_day(q: str) -> tuple[str, str, str] | None:
    m = _EXACT_DAY_YMD_RE.search(q)
    if not m:
        m = _EXACT_DAY_ISO_RE.search(q)
    if not m:
        return None
    y, mon, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        d = date(y, mon, day)
    except ValueError:
        return None
    next_d = d + timedelta(days=1)
    return (
        f"'{d.isoformat()} 00:00:00'",
        f"'{next_d.isoformat()} 00:00:00'",
        f"day_{y:04d}_{mon:02d}_{day:02d}",
    )


def _valid_calendar_date(year: int, month: int, day: int) -> bool:
    try:
        date(year, month, day)
        return True
    except ValueError:
        return False


def _day_window_sql_cur_year(month: int, day: int) -> tuple[str, str, str]:
    """无年份月日：永远取 YEAR(CURDATE())。"""
    start = f"DATE(CONCAT(YEAR(CURDATE()), '-{month:02d}-{day:02d}'))"
    end = f"DATE_ADD({start}, INTERVAL 1 DAY)"
    return start, end, f"day_cur_{month:02d}_{day:02d}"


def _day_window_sql_cur_month_day(day: int) -> tuple[str, str, str]:
    """仅日：永远取 YEAR/MONTH(CURDATE())。"""
    start = (
        f"STR_TO_DATE(CONCAT(DATE_FORMAT(CURDATE(), '%Y-%m-'), LPAD({day}, 2, '0')), '%Y-%m-%d')"
    )
    end = f"DATE_ADD({start}, INTERVAL 1 DAY)"
    return start, end, f"day_cur_m_{day:02d}"


def _try_month_day_without_year(q: str) -> tuple[str, str, str] | None:
    """6月25日、6/25、06-25、6.25 等（当年，左闭右开单日窗）。"""
    year = date.today().year
    for pat in (
        _MONTH_DAY_CN_RE,
        _MONTH_DAY_SLASH_RE,
        _MONTH_DAY_DASH_RE,
        _MONTH_DAY_DOT_RE,
    ):
        m = pat.search(q)
        if not m:
            continue
        mon, day = int(m.group(1)), int(m.group(2))
        if not _valid_calendar_date(year, mon, day):
            continue
        return _day_window_sql_cur_year(mon, day)
    return None


def _try_day_of_month_only(q: str) -> tuple[str, str, str] | None:
    """25日、25号（当年当月）。"""
    m = _DAY_ONLY_RE.search(q)
    if not m:
        return None
    day = int(m.group(1))
    today = date.today()
    if not _valid_calendar_date(today.year, today.month, day):
        return None
    return _day_window_sql_cur_month_day(day)


def _try_month_only(q: str) -> tuple[str, str, str] | None:
    m = _MONTH_ONLY_RE.search(q)
    if not m:
        return None
    mon = int(m.group(1))
    start = f"DATE(CONCAT(YEAR(CURDATE()), '-{mon:02d}-01'))"
    if mon == 12:
        end = "DATE_ADD(DATE(CONCAT(YEAR(CURDATE()), '-01-01')), INTERVAL 1 YEAR)"
    else:
        end = f"DATE(CONCAT(YEAR(CURDATE()), '-{mon + 1:02d}-01'))"
    return start, end, f"month_cur_{mon:02d}"


def extract_time_window_from_question(question: str) -> tuple[str, str, str] | None:
    """从问句提取 (start_sql_expr, end_sql_expr, tag)；与 NL2SQLChain 历史行为一致。"""
    q = (question or "").strip().lower()
    if not q:
        return None
    this_month_start = "DATE_FORMAT(CURDATE(), '%Y-%m-01')"
    this_year_start = "DATE_FORMAT(CURDATE(), '%Y-01-01')"
    this_week_start = "DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)"

    if "近一年" in q or "最近一年" in q or "过去一年" in q:
        return (
            "DATE_SUB(CURDATE(), INTERVAL 1 YEAR)",
            "DATE_ADD(CURDATE(), INTERVAL 1 DAY)",
            "recent_1_year",
        )
    if "最近一周" in q or "近一周" in q:
        return ("DATE_SUB(NOW(), INTERVAL 7 DAY)", "NOW()", "recent_7_days")
    if "最近七天" in q or "近七天" in q:
        return ("DATE_SUB(NOW(), INTERVAL 7 DAY)", "NOW()", "recent_7_days")
    if "最近半年" in q or "近半年" in q:
        return ("DATE_SUB(NOW(), INTERVAL 6 MONTH)", "NOW()", "recent_6_months")

    n_year = extract_numeric_window(q, ("年", "year", "years"))
    if n_year:
        return (
            f"DATE_SUB(CURDATE(), INTERVAL {n_year} YEAR)",
            "DATE_ADD(CURDATE(), INTERVAL 1 DAY)",
            f"recent_{n_year}_years",
        )

    n_day = extract_numeric_window(q, ("天", "day", "days"))
    if n_day:
        return (f"DATE_SUB(NOW(), INTERVAL {n_day} DAY)", "NOW()", f"recent_{n_day}_days")
    n_week = extract_numeric_window(q, ("周", "week", "weeks"))
    if n_week:
        return (f"DATE_SUB(NOW(), INTERVAL {n_week} WEEK)", "NOW()", f"recent_{n_week}_weeks")
    n_month = extract_numeric_window(q, ("月", "month", "months"))
    if n_month:
        return (f"DATE_SUB(NOW(), INTERVAL {n_month} MONTH)", "NOW()", f"recent_{n_month}_months")
    n_hour = extract_numeric_window(q, ("小时", "hour", "hours", "h"))
    if n_hour:
        return (f"DATE_SUB(NOW(), INTERVAL {n_hour} HOUR)", "NOW()", f"recent_{n_hour}_hours")
    n_min = extract_numeric_window(q, ("分钟", "minute", "minutes", "min"))
    if n_min:
        return (f"DATE_SUB(NOW(), INTERVAL {n_min} MINUTE)", "NOW()", f"recent_{n_min}_minutes")

    if "本周" in q or "这周" in q:
        return (this_week_start, f"DATE_ADD({this_week_start}, INTERVAL 7 DAY)", "this_week")
    if "上周" in q:
        return (f"DATE_SUB({this_week_start}, INTERVAL 7 DAY)", this_week_start, "last_week")
    if "本月" in q or "这个月" in q:
        return (this_month_start, f"DATE_ADD({this_month_start}, INTERVAL 1 MONTH)", "this_month")
    if "上月" in q or "上个月" in q:
        return (
            f"DATE_SUB({this_month_start}, INTERVAL 1 MONTH)",
            this_month_start,
            "last_month",
        )
    if "今年" in q or "本年" in q:
        return (this_year_start, f"DATE_ADD({this_year_start}, INTERVAL 1 YEAR)", "this_year")
    if "去年" in q:
        return (
            f"DATE_SUB({this_year_start}, INTERVAL 1 YEAR)",
            this_year_start,
            "last_year",
        )
    if "前年" in q:
        return (
            "DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 2 YEAR)",
            "DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR)",
            "year_before_last",
        )

    if "上半年" in q:
        return half_year_bounds_sql(which="first")
    if "下半年" in q:
        return half_year_bounds_sql(which="second")

    if "本季度" in q or "这个季度" in q or "这一季度" in q:
        return this_quarter_bounds_sql()
    if "上季度" in q:
        return last_quarter_bounds_sql()

    yq = _try_year_quarter(q)
    if yq:
        return yq
    oq = _try_ordinal_quarter(q)
    if oq:
        return oq

    if "大前天" in q:
        return (
            "DATE_SUB(CURDATE(), INTERVAL 3 DAY)",
            "DATE_SUB(CURDATE(), INTERVAL 2 DAY)",
            "three_days_ago",
        )
    if "今天" in q or "今日" in q:
        return ("CURDATE()", "DATE_ADD(CURDATE(), INTERVAL 1 DAY)", "today")
    if "昨天" in q or "昨日" in q:
        return ("DATE_SUB(CURDATE(), INTERVAL 1 DAY)", "CURDATE()", "yesterday")
    if "前天" in q or "前日" in q:
        return (
            "DATE_SUB(CURDATE(), INTERVAL 2 DAY)",
            "DATE_SUB(CURDATE(), INTERVAL 1 DAY)",
            "day_before_yesterday",
        )

    exact = _try_exact_day(q)
    if exact:
        return exact

    month_day = _try_month_day_without_year(q)
    if month_day:
        return month_day

    day_only = _try_day_of_month_only(q)
    if day_only:
        return day_only

    m_ym = re.search(r"(20\d{2})年(0?[1-9]|1[0-2])月", q)
    if not m_ym:
        m_ym = re.search(r"(20\d{2})-(0?[1-9]|1[0-2])(?!-\d{2})", q)
    if m_ym:
        y = int(m_ym.group(1))
        mon = int(m_ym.group(2))
        next_y = y + 1 if mon == 12 else y
        next_m = 1 if mon == 12 else mon + 1
        return (
            f"'{y:04d}-{mon:02d}-01 00:00:00'",
            f"'{next_y:04d}-{next_m:02d}-01 00:00:00'",
            f"month_{y:04d}_{mon:02d}",
        )

    month_only = _try_month_only(q)
    if month_only:
        return month_only

    m_year = re.search(r"(20\d{2})年", q)
    if m_year:
        y = m_year.group(1)
        return (f"'{y}-01-01 00:00:00'", f"'{int(y)+1}-01-01 00:00:00'", f"year_{y}")
    return None


def _fmt_display_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _day_start(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _day_end(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59))


def _subtract_months(d: date, months: int) -> date:
    y, m, day = d.year, d.month - months, d.day
    while m <= 0:
        m += 12
        y -= 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, min(day, last))


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    last = calendar.monthrange(year, month)[1]
    return _day_start(date(year, month, 1)), _day_end(date(year, month, last))


def _quarter_bounds_display(year: int, quarter: int) -> tuple[datetime, datetime]:
    sm = _quarter_start_month(quarter)
    end_y, end_m = _quarter_end_ym(year, quarter)
    start_d = date(year, sm, 1)
    end_d = date(end_y, end_m, 1) - timedelta(days=1)
    return _day_start(start_d), _day_end(end_d)


def _current_quarter(ref: date) -> int:
    return (ref.month - 1) // 3 + 1


def _tag_to_display_bounds(tag: str, ref: datetime) -> tuple[datetime, datetime] | None:
    d = ref.date()

    if tag == "today":
        return _day_start(d), _day_end(d)
    if tag == "yesterday":
        day = d - timedelta(days=1)
        return _day_start(day), _day_end(day)
    if tag == "day_before_yesterday":
        day = d - timedelta(days=2)
        return _day_start(day), _day_end(day)
    if tag == "three_days_ago":
        day = d - timedelta(days=3)
        return _day_start(day), _day_end(day)

    if tag == "this_week":
        monday = d - timedelta(days=d.weekday())
        sunday = monday + timedelta(days=6)
        return _day_start(monday), _day_end(sunday)
    if tag == "last_week":
        monday = d - timedelta(days=d.weekday()) - timedelta(days=7)
        sunday = monday + timedelta(days=6)
        return _day_start(monday), _day_end(sunday)

    if tag == "this_month":
        return _month_bounds(d.year, d.month)
    if tag == "last_month":
        prev = _subtract_months(d.replace(day=1), 1)
        return _month_bounds(prev.year, prev.month)

    if tag == "this_year":
        return _day_start(date(d.year, 1, 1)), _day_end(date(d.year, 12, 31))
    if tag == "last_year":
        y = d.year - 1
        return _day_start(date(y, 1, 1)), _day_end(date(y, 12, 31))
    if tag == "year_before_last":
        y = d.year - 2
        return _day_start(date(y, 1, 1)), _day_end(date(y, 12, 31))

    if tag == "half_first":
        return _day_start(date(d.year, 1, 1)), _day_end(date(d.year, 6, 30))
    if tag == "half_second":
        return _day_start(date(d.year, 7, 1)), _day_end(date(d.year, 12, 31))

    if tag == "this_quarter":
        cq = _current_quarter(d)
        return _quarter_bounds_display(d.year, cq)
    if tag == "last_quarter":
        cq = _current_quarter(d)
        y, q = d.year, cq - 1
        if q < 1:
            y, q = y - 1, 4
        return _quarter_bounds_display(y, q)

    if tag.startswith("quarter_"):
        parts = tag.split("_")
        if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
            return _quarter_bounds_display(int(parts[1]), int(parts[2]))
    if tag.startswith("quarter_cur_"):
        qn = int(tag.split("_")[-1])
        return _quarter_bounds_display(d.year, qn)

    if tag.startswith("day_cur_m_"):
        try:
            day_n = int(tag.rsplit("_", 1)[-1])
            day_d = date(d.year, d.month, day_n)
            return _day_start(day_d), _day_end(day_d)
        except ValueError:
            pass

    m_day_cur = re.fullmatch(r"day_cur_(\d{2})_(\d{2})", tag)
    if m_day_cur:
        try:
            mon, day_n = int(m_day_cur.group(1)), int(m_day_cur.group(2))
            day_d = date(d.year, mon, day_n)
            return _day_start(day_d), _day_end(day_d)
        except ValueError:
            pass

    if tag.startswith("day_"):
        parts = tag.split("_")
        if len(parts) >= 4 and parts[1].isdigit():
            try:
                day_d = date(int(parts[1]), int(parts[2]), int(parts[3]))
                return _day_start(day_d), _day_end(day_d)
            except ValueError:
                pass

    if tag.startswith("month_cur_"):
        mon = int(tag.split("_")[-1])
        return _month_bounds(d.year, mon)

    if tag.startswith("year_"):
        y = int(tag.split("_", 1)[1])
        return _day_start(date(y, 1, 1)), _day_end(date(y, 12, 31))

    if tag.startswith("month_"):
        parts = tag.split("_")
        if len(parts) >= 3:
            y, mon = int(parts[1]), int(parts[2])
            return _month_bounds(y, mon)

    if tag == "recent_1_year":
        try:
            start_d = d.replace(year=d.year - 1)
        except ValueError:
            start_d = date(d.year - 1, 2, 28)
        return _day_start(start_d), _day_end(d)

    if tag == "recent_6_months":
        start_d = _subtract_months(d, 6)
        return _day_start(start_d), _day_end(d)

    m_roll_years = re.fullmatch(r"recent_(\d+)_years", tag)
    if m_roll_years:
        n = int(m_roll_years.group(1))
        try:
            start_d = d.replace(year=d.year - n)
        except ValueError:
            start_d = date(d.year - n, 2, 28)
        return _day_start(start_d), _day_end(d)

    m_roll = re.fullmatch(r"recent_(\d+)_(days|weeks|months|hours|minutes)", tag)
    if m_roll:
        n = int(m_roll.group(1))
        unit = m_roll.group(2)
        if unit == "days":
            return ref - timedelta(days=n), ref
        if unit == "weeks":
            return ref - timedelta(weeks=n), ref
        if unit == "months":
            start_d = _subtract_months(d, n)
            return _day_start(start_d), ref
        if unit == "hours":
            return ref - timedelta(hours=n), ref
        if unit == "minutes":
            return ref - timedelta(minutes=n), ref

    if tag == "recent_7_days":
        return ref - timedelta(days=7), ref

    return None


def extract_time_window_tag(question: str) -> str | None:
    win = extract_time_window_from_question(question)
    if not win:
        return None
    return str(win[2])


def default_time_window_sql_fallback() -> tuple[str, str, str]:
    """问句未解析到时间窗时，NL2SQL 默认按昨天（左闭右开，与显式「昨天」一致）。"""
    return ("DATE_SUB(CURDATE(), INTERVAL 1 DAY)", "CURDATE()", DEFAULT_TIME_WINDOW_TAG)


def default_statistical_time_range_display(
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """未识别时间表达时的展示边界：昨天 00:00:00～23:59:59。"""
    ref = now or datetime.now()
    day = ref.date() - timedelta(days=1)
    return _fmt_display_dt(_day_start(day)), _fmt_display_dt(_day_end(day))


def resolve_statistical_time_range_display(
    question: str,
    *,
    now: datetime | None = None,
) -> tuple[str, str]:
    """
    从用户问句解析统计口径起止时间，供报告第一章展示。

    未解析到时间表达时，默认按昨天 00:00:00～23:59:59。
    """
    tag = extract_time_window_tag(question)
    if not tag:
        return default_statistical_time_range_display(now=now)
    ref = now or datetime.now()
    bounds = _tag_to_display_bounds(tag, ref)
    if bounds is None:
        return default_statistical_time_range_display(now=now)
    start, end = bounds
    return _fmt_display_dt(start), _fmt_display_dt(end)
