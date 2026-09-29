"""「最新」口径：每监测实体取 data_time 最大一行（DISTINCT ON / MAX），禁止日历日窗自由发挥。"""

from __future__ import annotations

import re
from typing import Any

LATEST_PER_STATION_TAG = "latest_per_station"

_LATEST_QUESTION_RE = re.compile(r"最新|每站最新|各站最新")

# 日历日窗谓词（字面量 / CURRENT_DATE / INTERVAL / DATE_SUB 等）
_DATA_TIME_COL = r"(?:[a-zA-Z_][\w]*\.)?data_time"
_CALENDAR_RHS = (
    r"(?:"
    r"'[^']+'"
    r"|CURRENT_DATE(?:\s*[+\-]\s*INTERVAL\s+'[^']+')?"
    r"|CURRENT_TIMESTAMP(?:\s*[+\-]\s*INTERVAL\s+'[^']+')?"
    r"|NOW\s*\(\s*\)(?:\s*[+\-]\s*INTERVAL\s+'[^']+')?"
    r"|CURDATE\s*\(\s*\)"
    r"|DATE_SUB\s*\([^)]+\)"
    r"|DATE_ADD\s*\([^)]+\)"
    r"|\([^)]*INTERVAL[^)]*\)"
    r")"
)
_CALENDAR_PRED_RE = re.compile(
    rf"(?is)\b({_DATA_TIME_COL})\s*(?:"
    rf">=\s*{_CALENDAR_RHS}"
    rf"|<=\s*{_CALENDAR_RHS}"
    rf"|<\s*{_CALENDAR_RHS}"
    rf"|>\s*{_CALENDAR_RHS}"
    rf"|=\s*{_CALENDAR_RHS}"
    rf"|BETWEEN\s*{_CALENDAR_RHS}\s+AND\s*{_CALENDAR_RHS}"
    rf")"
)

_DISTINCT_ON_RE = re.compile(r"(?is)\bDISTINCT\s+ON\s*\(")
_MAX_DATA_TIME_RE = re.compile(r"(?is)\bMAX\s*\(\s*(?:[a-zA-Z_][\w]*\.)?data_time\s*\)")
_SELECT_RE = re.compile(r"(?is)\bSELECT\b(\s+DISTINCT\b)?(?!\s+ON\b)")


def is_latest_per_station_question(question: str) -> bool:
    return bool(_LATEST_QUESTION_RE.search(question or ""))


def latest_per_station_sql_window() -> tuple[str, str, str]:
    """无日历边界的哨兵时间窗；改写阶段走每实体最新模板。"""
    return ("", "", LATEST_PER_STATION_TAG)


def latest_per_station_display_range() -> tuple[str, str]:
    return ("每站最新一条", "每站最新一条")


def format_latest_per_station_prompt_rule(*, grain: str = "station_name") -> str:
    g = (grain or "station_name").strip() or "station_name"
    return (
        f"- 【最新模板·强制】时间口径={LATEST_PER_STATION_TAG}：禁止写日历日窗（昨天/今天/BETWEEN）；"
        f"按监测实体取每实体 data_time 最大一行。"
        f"PostgreSQL 优先：SELECT DISTINCT ON ({g}) … ORDER BY {g}, data_time DESC；"
        f"或等价 JOIN (SELECT {g}, MAX(data_time) AS max_dt … GROUP BY {g})。"
        f"禁止用嵌套 IN (SELECT …) 表达「最新」或凑名单。"
    )


def resolve_latest_grain(suggested_filters: list[Any] | None = None) -> str:
    cols: list[str] = []
    for raw in suggested_filters or []:
        if not isinstance(raw, dict):
            continue
        col = str(raw.get("column") or "").strip().lower()
        if col:
            cols.append(col)
    if "station_name" in cols:
        return "station_name"
    if "project_name" in cols:
        return "project_name"
    if "name" in cols:
        return "name"
    return "station_name"


def _excise_predicate(sql: str, start: int, end: int) -> str:
    left = sql[:start]
    right = sql[end:]
    right_m = re.match(r"(?is)\s+(AND|OR)\b\s*", right)
    if right_m:
        right = right[right_m.end() :]
        return (left.rstrip() + " " + right.lstrip()) if right.lstrip() else left.rstrip() + right
    left_m = re.search(r"(?is)\s+(AND|OR)\s*$", left)
    if left_m:
        left = left[: left_m.start()]
        return left.rstrip() + ((" " + right.lstrip()) if right.lstrip() else right)
    where_m = re.search(r"(?is)\bWHERE\s*$", left)
    if where_m:
        left = left[: where_m.start()]
        return left.rstrip() + ((" " + right.lstrip()) if right.lstrip() else right)
    return left.rstrip() + ((" " + right.lstrip()) if right.lstrip() else right)


def strip_calendar_data_time_predicates(sql: str) -> tuple[str, bool]:
    """去掉 data_time 上的日历日窗谓词，保留其它 WHERE。"""
    rewritten = sql or ""
    changed = False
    while True:
        m = _CALENDAR_PRED_RE.search(rewritten)
        if not m:
            break
        rewritten = _excise_predicate(rewritten, m.start(), m.end())
        changed = True
    rewritten = re.sub(r"(?is)\bWHERE\s+(AND|OR)\b", "WHERE ", rewritten)
    rewritten = re.sub(r"(?is)\bWHERE\s*(?=(?:GROUP|ORDER|LIMIT)\b|$)", " ", rewritten)
    return rewritten, changed


def _ensure_order_by_latest(sql: str, grain: str) -> str:
    order_clause = f"ORDER BY {grain}, data_time DESC"
    if re.search(r"(?is)\bORDER\s+BY\b", sql):
        return re.sub(
            r"(?is)\bORDER\s+BY\b[\s\S]*?(?=\bLIMIT\b|;|$)",
            order_clause + " ",
            sql,
            count=1,
        )
    if re.search(r"(?is)\bLIMIT\b", sql):
        return re.sub(r"(?is)\bLIMIT\b", order_clause + " LIMIT", sql, count=1)
    body = sql.rstrip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    return f"{body} {order_clause}"


def _inject_distinct_on(sql: str, grain: str) -> str:
    m = _SELECT_RE.search(sql)
    if not m:
        return sql
    injected = f"SELECT DISTINCT ON ({grain})"
    rest = sql[m.end() :]
    return _ensure_order_by_latest(injected + rest, grain)


def rewrite_sql_for_latest_per_station(
    sql: str,
    *,
    grain: str = "station_name",
    prefer_distinct_on: bool = True,
) -> tuple[str, list[str]]:
    """
    将 SQL 对齐「每实体最新」模板：
    - 剥日历 data_time 窗；
    - 若已有 DISTINCT ON / MAX(data_time) 则保留；
    - PostgreSQL 缺省时注入 DISTINCT ON (grain) + ORDER BY grain, data_time DESC。
    """
    notes: list[str] = []
    rewritten = sql or ""
    g = (grain or "station_name").strip() or "station_name"

    rewritten, stripped = strip_calendar_data_time_predicates(rewritten)
    if stripped:
        notes.append("latest_per_station_strip_calendar")

    if _DISTINCT_ON_RE.search(rewritten):
        rewritten = _ensure_order_by_latest(rewritten, g)
        notes.append("latest_per_station_distinct_on_present")
        return rewritten, notes
    if _MAX_DATA_TIME_RE.search(rewritten):
        notes.append("latest_per_station_max_present")
        return rewritten, notes

    if prefer_distinct_on:
        rewritten2 = _inject_distinct_on(rewritten, g)
        if rewritten2 != rewritten:
            notes.append(f"latest_per_station_inject_distinct_on:{g}")
            return rewritten2, notes

    notes.append("latest_per_station_no_template_applied")
    return rewritten, notes
