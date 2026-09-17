"""将 SchemaLink.suggested_filters 强制写回 SQL（权威名单，不依赖 LLM 抄写）。"""

from __future__ import annotations

import re
from typing import Any

# 参与强制改写的来源（与 schema_linker 写入的 source 对齐）
_AUTHORITATIVE_SOURCES = frozenset(
    {
        "device_station_map",
        "fcb_layer0",
        "fcb_compress",
        "semantic",
    }
)

# 同列多 filter 时的优先级（越大越优先）
_SOURCE_PRIORITY: dict[str, int] = {
    "device_station_map": 40,
    "fcb_compress": 30,
    "fcb_layer0": 20,
    "semantic": 10,
}

# 不含 station_id：事实表遗留主键列禁止强制改写/注入
_FORCE_COLUMNS = frozenset({"name", "project_name", "station_name", "area"})

# 事实表清洗遗留联合主键列：生成 SQL 后一律剥掉过滤谓词
_LEGACY_FACT_PK_COLUMNS = ("station_id", "data_id")


def _sql_string_literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _format_values_list(values: list[Any]) -> str:
    return ",".join(_sql_string_literal(v) for v in values)


def _normalize_filter(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    column = str(raw.get("column") or "").strip().lower()
    if column not in _FORCE_COLUMNS:
        return None
    source = str(raw.get("source") or "").strip()
    if source not in _AUTHORITATIVE_SOURCES:
        return None
    op = str(raw.get("op") or "=").strip().lower()
    if op not in {"=", "in"}:
        return None
    value = raw.get("value")
    table = str(raw.get("table") or "").strip()
    if op == "in":
        if isinstance(value, list):
            values = [v for v in value if v is not None and str(v).strip() != ""]
        elif value is None or str(value).strip() == "":
            values = []
        else:
            values = [value]
        if not values:
            # 空名单（如 gnss 占位）不改写，避免 IN ()
            return None
        return {
            "table": table,
            "column": column,
            "op": "in",
            "values": values,
            "source": source,
        }
    if value is None or str(value).strip() == "":
        return None
    return {
        "table": table,
        "column": column,
        "op": "=",
        "values": [value],
        "source": source,
    }


def _pick_filters(filters: list[Any]) -> list[dict[str, Any]]:
    """按列去重，保留高优先级 authoritative filter。"""
    best: dict[str, dict[str, Any]] = {}
    for raw in filters or []:
        item = _normalize_filter(raw if isinstance(raw, dict) else {})
        if item is None:
            continue
        key = item["column"]
        prev = best.get(key)
        if prev is None or _SOURCE_PRIORITY.get(item["source"], 0) >= _SOURCE_PRIORITY.get(
            prev["source"], 0
        ):
            best[key] = item
    # 稳定顺序：area → name/project_name → station_name
    order = {"area": 0, "name": 1, "project_name": 2, "station_name": 3}
    return sorted(best.values(), key=lambda x: (order.get(x["column"], 9), x["column"]))


def _build_predicate(col_ref: str, item: dict[str, Any]) -> str:
    if item["op"] == "in":
        return f"{col_ref} IN ({_format_values_list(item['values'])})"
    return f"{col_ref} = {_sql_string_literal(item['values'][0])}"


def _match_span_end_for_in(sql: str, open_paren_idx: int) -> int | None:
    """从 IN( 的 '(' 下标起，找到配对的 ')'。"""
    depth = 0
    in_str = False
    i = open_paren_idx
    while i < len(sql):
        ch = sql[i]
        if in_str:
            if ch == "'":
                if i + 1 < len(sql) and sql[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch == "'":
            in_str = True
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


_COL_IN_RE_TMPL = r"(?is)(?:\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\.\s*)?{col}\s+IN\s*\("
_COL_EQ_RE_TMPL = r"(?is)(?:\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\.\s*)?{col}\s*=\s*'([^']*)'"
_COL_LIKE_RE_TMPL = (
    r"(?is)(?:\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\.\s*)?{col}\s+LIKE\s+"
    r"(?:CONCAT\s*\([^)]*\)|'[^']*')"
)


def _replace_column_predicate(sql: str, item: dict[str, Any]) -> tuple[str, bool]:
    """替换 SQL 中已有的该列 = / IN / LIKE 谓词；成功返回 (new_sql, True)。"""
    col = re.escape(item["column"])
    # 优先替换 IN
    in_re = re.compile(_COL_IN_RE_TMPL.format(col=col))
    m = in_re.search(sql)
    if m:
        open_idx = m.end() - 1  # points at '('
        close_idx = _match_span_end_for_in(sql, open_idx)
        if close_idx is not None:
            alias = m.group(1)
            col_ref = f"{alias}.{item['column']}" if alias else item["column"]
            pred = _build_predicate(col_ref, item)
            return sql[: m.start()] + pred + sql[close_idx + 1 :], True

    # 再替换 = / LIKE（区县常被放宽成 LIKE）
    for tmpl in (_COL_EQ_RE_TMPL, _COL_LIKE_RE_TMPL):
        eq_re = re.compile(tmpl.format(col=col))
        m2 = eq_re.search(sql)
        if m2:
            alias = m2.group(1)
            col_ref = f"{alias}.{item['column']}" if alias else item["column"]
            # IN 名单用 IN；单值用 =
            if item["op"] == "in":
                pred = _build_predicate(col_ref, item)
            else:
                pred = _build_predicate(col_ref, {**item, "op": "="})
            return sql[: m2.start()] + pred + sql[m2.end() :], True
    return sql, False


def _find_table_alias(sql: str, table: str) -> str | None:
    if not table:
        return None
    t = re.escape(table)
    patterns = (
        rf"(?is)\b{t}\s+AS\s+([a-zA-Z_][a-zA-Z0-9_]*)\b",
        rf"(?is)\b{t}\s+([a-zA-Z_][a-zA-Z0-9_]*)\b",
    )
    for pat in patterns:
        m = re.search(pat, sql)
        if m:
            alias = m.group(1)
            # 避免把 JOIN/WHERE/ON 当别名
            if alias.lower() in {"as", "on", "where", "join", "left", "right", "inner", "outer", "cross"}:
                continue
            return alias
    # FROM t_station 无别名
    if re.search(rf"(?is)\b{t}\b", sql):
        return None
    return None


def _inject_predicate(sql: str, item: dict[str, Any]) -> tuple[str, bool]:
    """WHERE 中尚无该列条件时追加 AND 谓词。"""
    alias = _find_table_alias(sql, item.get("table") or "")
    if alias:
        col_ref = f"{alias}.{item['column']}"
    else:
        col_ref = item["column"]
    pred = _build_predicate(col_ref, item)

    where_m = re.search(r"(?is)\bWHERE\b", sql)
    if where_m:
        # 插到 WHERE 后第一个条件前用 AND 拼接：追加在 WHERE 子句末尾（ORDER/GROUP/LIMIT 之前）
        tail_re = re.compile(
            r"(?is)\b(GROUP\s+BY|ORDER\s+BY|LIMIT|OFFSET|HAVING|UNION|INTERSECT|EXCEPT)\b"
        )
        start = where_m.end()
        tail_m = tail_re.search(sql, start)
        end = tail_m.start() if tail_m else len(sql)
        where_body = sql[start:end].rstrip()
        if where_body:
            injected = sql[:start] + " " + where_body + " AND " + pred + " " + sql[end:]
        else:
            injected = sql[:start] + " " + pred + " " + sql[end:]
        return injected, True

    # 无 WHERE：在 ORDER/GROUP/LIMIT 前插入
    tail_re = re.compile(
        r"(?is)\b(GROUP\s+BY|ORDER\s+BY|LIMIT|OFFSET|HAVING|UNION|INTERSECT|EXCEPT)\b"
    )
    tail_m = tail_re.search(sql)
    if tail_m:
        return sql[: tail_m.start()].rstrip() + " WHERE " + pred + " " + sql[tail_m.start() :], True
    return sql.rstrip() + " WHERE " + pred, True


def strip_legacy_fact_pk_predicates(sql: str) -> tuple[str, list[str]]:
    """剥掉事实表遗留主键列 ``station_id`` / ``data_id`` 的 WHERE 谓词（= / IN）。"""
    if not sql:
        return sql, []
    rewritten = sql
    notes: list[str] = []
    for col in _LEGACY_FACT_PK_COLUMNS:
        while True:
            rewritten2, removed = _remove_column_predicate(rewritten, col)
            if not removed:
                break
            rewritten = rewritten2
            notes.append(f"strip_legacy_pk:{col}")
    return rewritten, notes


def _remove_column_predicate(sql: str, column: str) -> tuple[str, bool]:
    """删除 SQL 中该列的 = / IN 谓词，并清理多余 AND/OR/WHERE。"""
    col = re.escape(column)

    in_re = re.compile(_COL_IN_RE_TMPL.format(col=col))
    m = in_re.search(sql)
    if m:
        open_idx = m.end() - 1
        close_idx = _match_span_end_for_in(sql, open_idx)
        if close_idx is not None:
            return _excise_predicate_span(sql, m.start(), close_idx + 1), True

    eq_re = re.compile(_COL_EQ_RE_TMPL.format(col=col))
    m2 = eq_re.search(sql)
    if m2:
        return _excise_predicate_span(sql, m2.start(), m2.end()), True
    return sql, False


def _excise_predicate_span(sql: str, start: int, end: int) -> str:
    """去掉 [start,end) 谓词，并吞掉紧邻的 AND/OR；避免留下空 WHERE。"""
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


def rewrite_sql_with_suggested_filters(
    sql: str,
    suggested_filters: list[Any] | None,
) -> tuple[str, list[str]]:
    """
    用 SchemaLink.suggested_filters 强制对齐 SQL 中的范围谓词。

    - 已有 ``col IN (...)`` / ``=`` / ``LIKE``：替换为权威名单或等值；
    - 缺失：在 WHERE 中追加；
    - 空 IN（如 gnss 占位）：跳过，不生成 ``IN ()``；
    - 始终剥掉事实表遗留主键 ``station_id`` / ``data_id`` 过滤谓词。
    """
    notes: list[str] = []
    rewritten = sql or ""

    if rewritten and suggested_filters:
        picked = _pick_filters(list(suggested_filters))
        for item in picked:
            rewritten2, replaced = _replace_column_predicate(rewritten, item)
            if replaced:
                rewritten = rewritten2
                notes.append(
                    f"suggested_filter_replace:{item['source']}:{item['column']}:{item['op']}:{len(item['values'])}"
                )
                continue
            rewritten3, injected = _inject_predicate(rewritten, item)
            if injected:
                rewritten = rewritten3
                notes.append(
                    f"suggested_filter_inject:{item['source']}:{item['column']}:{item['op']}:{len(item['values'])}"
                )

    stripped, strip_notes = strip_legacy_fact_pk_predicates(rewritten)
    return stripped, notes + strip_notes


def suggested_filters_from_parsed_intent(parsed_intent: dict[str, Any] | None) -> list[Any]:
    if not isinstance(parsed_intent, dict):
        return []
    linked = parsed_intent.get("linked_schema")
    if not isinstance(linked, dict):
        return []
    filters = linked.get("suggested_filters")
    return list(filters) if isinstance(filters, list) else []
