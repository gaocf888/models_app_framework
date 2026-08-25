"""SQL 方言适配（TiDB/MySQL ↔ PostgreSQL）。"""

from __future__ import annotations

import os
import re

from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile

_KNOWN_PG_REPLACEMENTS: dict[str, str] = {
    "DATE_SUB(CURDATE(), INTERVAL 1 DAY)": "(CURRENT_DATE - INTERVAL '1 day')",
    "CURDATE()": "CURRENT_DATE",
    "DATE_FORMAT(CURDATE(), '%Y-%m-01')": "(date_trunc('month', CURRENT_DATE)::date)",
    "DATE_FORMAT(CURDATE(), '%Y-01-01')": "(date_trunc('year', CURRENT_DATE)::date)",
    "DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)": "(date_trunc('week', CURRENT_DATE)::date)",
    "NOW()": "NOW()",
}


def get_sql_dialect() -> str:
    profile = get_nl2sql_business_profile()
    if profile and profile.sql_dialect:
        return profile.sql_dialect.strip().lower()
    return (os.getenv("NL2SQL_SQL_DIALECT") or "tidb").strip().lower()


def is_postgres_dialect() -> bool:
    d = get_sql_dialect()
    return d in {"postgres", "postgresql", "pg"}


def adapt_time_expr(expr: str) -> str:
    """将 MySQL 风格时间表达式适配为目标方言。"""
    if not expr or not is_postgres_dialect():
        return expr
    return adapt_mysql_time_expr_to_postgres(expr)


def adapt_mysql_time_expr_to_postgres(expr: str) -> str:
    out = expr.strip()
    for src, dst in _KNOWN_PG_REPLACEMENTS.items():
        out = out.replace(src, dst)

    out = out.replace(
        "DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')",
        "(date_trunc('month', CURRENT_DATE)::date)",
    )
    out = out.replace(
        "DATE_FORMAT(CURRENT_DATE, '%Y-01-01')",
        "(date_trunc('year', CURRENT_DATE)::date)",
    )

    out = re.sub(
        r"YEAR\s*\(\s*CURRENT_DATE\s*\)",
        "EXTRACT(YEAR FROM CURRENT_DATE)::int",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"MONTH\s*\(\s*CURRENT_DATE\s*\)",
        "EXTRACT(MONTH FROM CURRENT_DATE)::int",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"YEAR\s*\(\s*CURDATE\s*\(\s*\)\s*\)",
        "EXTRACT(YEAR FROM CURRENT_DATE)::int",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"MONTH\s*\(\s*CURDATE\s*\(\s*\)\s*\)",
        "EXTRACT(MONTH FROM CURRENT_DATE)::int",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"WEEKDAY\s*\(\s*CURDATE\s*\(\s*\)\s*\)",
        "EXTRACT(DOW FROM CURRENT_DATE)::int",
        out,
        flags=re.IGNORECASE,
    )

    # DATE(CONCAT(YEAR(CURDATE()), '-MM-01')) → make_date(...)
    def _date_concat_year_month(m: re.Match[str]) -> str:
        month = int(m.group(1))
        return f"make_date(EXTRACT(YEAR FROM CURRENT_DATE)::int, {month}, 1)"

    for pat in (
        r"DATE\s*\(\s*CONCAT\s*\(\s*YEAR\s*\(\s*CURDATE\s*\(\s*\)\s*\)\s*,\s*'-(\d{2})-01'\s*\)\s*\)",
        r"DATE\s*\(\s*CONCAT\s*\(\s*EXTRACT\(YEAR FROM CURRENT_DATE\)::int\s*,\s*'-(\d{2})-01'\s*\)\s*\)",
    ):
        out = re.sub(pat, _date_concat_year_month, out, flags=re.IGNORECASE)

    # STR_TO_DATE(CONCAT(DATE_FORMAT(CURDATE(), '%Y-%m-'), LPAD(n, 2, '0')), '%Y-%m-%d')
    def _str_to_date_month_day(m: re.Match[str]) -> str:
        day = int(m.group(1))
        return (
            "make_date(EXTRACT(YEAR FROM CURRENT_DATE)::int, "
            f"EXTRACT(MONTH FROM CURRENT_DATE)::int, {day})"
        )

    out = re.sub(
        r"STR_TO_DATE\s*\(\s*CONCAT\s*\(\s*DATE_FORMAT\s*\(\s*CURDATE\s*\(\s*\)\s*,\s*'%Y-%m-'\s*\)\s*,\s*"
        r"LPAD\s*\(\s*(\d+)\s*,\s*2\s*,\s*'0'\s*\)\s*\)\s*,\s*'%Y-%m-%d'\s*\)",
        _str_to_date_month_day,
        out,
        flags=re.IGNORECASE,
    )

    _this_quarter_start = (
        "(date_trunc('month', CURRENT_DATE)::date "
        "- ((EXTRACT(MONTH FROM CURRENT_DATE)::int - 1) % 3) * INTERVAL '1 month')"
    )
    _quarter_patterns = (
        r"DATE_SUB\s*\(\s*DATE_FORMAT\s*\(\s*CURRENT_DATE\s*,\s*'%Y-%m-01'\s*\)\s*,\s*"
        r"INTERVAL\s*\(\(\s*MONTH\s*\(\s*CURRENT_DATE\s*\)\s*-\s*1\s*\)\s*%\s*3\s*\)\s*MONTH\s*\)",
        r"DATE_SUB\s*\(\s*\(date_trunc\('month', CURRENT_DATE\)::date\)\s*,\s*"
        r"INTERVAL\s*\(\(\s*EXTRACT\(MONTH FROM CURRENT_DATE\)::int\s*-\s*1\s*\)\s*%\s*3\s*\)\s*MONTH\s*\)",
    )
    for pat in _quarter_patterns:
        out = re.sub(pat, _this_quarter_start, out, flags=re.IGNORECASE)

    # DATE_SUB(expr, INTERVAL n UNIT)
    def _date_sub(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        n = m.group(2)
        unit = m.group(3).lower()
        unit_map = {
            "day": "day",
            "days": "days",
            "week": "week",
            "weeks": "weeks",
            "month": "month",
            "months": "months",
            "year": "year",
            "years": "years",
            "hour": "hour",
            "hours": "hours",
            "minute": "minute",
            "minutes": "minutes",
        }
        pg_unit = unit_map.get(unit, unit)
        return f"({adapt_mysql_time_expr_to_postgres(inner)} - INTERVAL '{n} {pg_unit}')"

    out = re.sub(
        r"DATE_SUB\(([^,]+),\s*INTERVAL\s+(\d+)\s+(\w+)\)",
        _date_sub,
        out,
        flags=re.IGNORECASE,
    )

    def _date_add(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        n = m.group(2)
        unit = m.group(3).lower()
        unit_map = {
            "day": "day",
            "days": "days",
            "week": "week",
            "weeks": "weeks",
            "month": "month",
            "months": "months",
            "year": "year",
            "years": "years",
            "hour": "hour",
            "hours": "hours",
            "minute": "minute",
            "minutes": "minutes",
        }
        pg_unit = unit_map.get(unit, unit)
        return f"({adapt_mysql_time_expr_to_postgres(inner)} + INTERVAL '{n} {pg_unit}')"

    out = re.sub(
        r"DATE_ADD\(([^,]+),\s*INTERVAL\s+(\d+)\s+(\w+)\)",
        _date_add,
        out,
        flags=re.IGNORECASE,
    )

    out = re.sub(r"\bDATE\s*\(\s*'([^']+)'\s*\)", r"'\1'::date", out, flags=re.IGNORECASE)
    return out


def adapt_time_window(start_expr: str, end_expr: str) -> tuple[str, str]:
    return adapt_time_expr(start_expr), adapt_time_expr(end_expr)
