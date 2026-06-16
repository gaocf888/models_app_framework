from __future__ import annotations

import re
from typing import Any

from app.nl2sql.intent_config import scope_sql_rewrite_enabled as _scope_sql_rewrite_enabled

_SCOPE_STRING_PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    ("device_keyword", "device_name"),
    ("piperow_keyword", "piperow_name"),
)

_PIPEROW_LIKE_ALIAS: dict[str, str] = {
    "第一屏": "前屏",
    "第二屏": "后屏",
}

_INT_PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    ("row_no", "row_no"),
    ("tube_no", "tube_no"),
)

_LIKE_CONCAT_PIPEROW_RE = re.compile(
    r"(?i)(\b[a-zA-Z_][a-zA-Z0-9_\.]*)\s+LIKE\s+CONCAT\s*\(\s*'%'\s*,\s*'([^']+)'\s*,\s*'%'\s*\)"
)


def scope_sql_rewrite_enabled() -> bool:
    return _scope_sql_rewrite_enabled()


def _has_placeholder(sql: str, name: str) -> bool:
    return bool(re.search(rf"@{re.escape(name)}\b", sql, re.IGNORECASE))


def _sql_string_literal(value: str | None) -> str:
    if value is None:
        return "''"
    return "'" + str(value).replace("'", "''") + "'"


def _scope_value(scopes: dict[str, Any], scope_key: str) -> str | None:
    raw = scopes.get(scope_key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _rewrite_string_placeholder(
    sql: str,
    *,
    placeholder: str,
    scope_key: str,
    scopes: dict[str, Any],
) -> tuple[str, list[str]]:
    if not _has_placeholder(sql, placeholder):
        return sql, []
    value = _scope_value(scopes, scope_key)
    replacement = _sql_string_literal(value)
    note = (
        f"{placeholder}_placeholder_empty"
        if value is None
        else f"{placeholder}_placeholder_single"
    )
    rewritten = re.sub(rf"@{placeholder}\b", replacement, sql, flags=re.IGNORECASE)
    return rewritten, [note]


def _rewrite_int_placeholder(
    sql: str,
    *,
    placeholder: str,
    scope_key: str,
    scopes: dict[str, Any],
) -> tuple[str, list[str]]:
    if not _has_placeholder(sql, placeholder):
        return sql, []
    raw = scopes.get(scope_key)
    if raw is None:
        replacement = "NULL"
        note = f"{placeholder}_placeholder_skip"
    else:
        try:
            n = int(raw)
        except (TypeError, ValueError):
            replacement = "NULL"
            note = f"{placeholder}_placeholder_skip"
        else:
            if n <= 0:
                replacement = "NULL"
                note = f"{placeholder}_placeholder_skip"
            else:
                replacement = str(n)
                note = f"{placeholder}_placeholder_single"
    rewritten = re.sub(rf"@{placeholder}\b", replacement, sql, flags=re.IGNORECASE)
    return rewritten, [note]


def _expand_piperow_like_aliases(sql: str, piperow_name: str | None) -> tuple[str, list[str]]:
    """第一屏/第二屏 LIKE 展开为 OR 前屏/后屏别名（台账口语不一致）。"""
    if not piperow_name:
        return sql, []
    alias = _PIPEROW_LIKE_ALIAS.get(piperow_name)
    if not alias:
        return sql, []

    notes: list[str] = []
    safe_main = piperow_name.replace("'", "''")
    safe_alias = alias.replace("'", "''")

    def _repl(m: re.Match[str]) -> str:
        col = m.group(1)
        matched = m.group(2)
        if matched != piperow_name:
            return m.group(0)
        notes.append("piperow_keyword_alias_or")
        return (
            f"({col} LIKE CONCAT('%', '{safe_main}', '%') "
            f"OR {col} LIKE CONCAT('%', '{safe_alias}', '%'))"
        )

    rewritten = _LIKE_CONCAT_PIPEROW_RE.sub(_repl, sql)
    return rewritten, notes


def rewrite_scope_sql_placeholders(
    sql: str,
    scopes: dict[str, Any],
) -> tuple[str, list[str]]:
    """
    将 scope 占位符替换为 SQL 字面量（Phase 2）。

    - 字符串类（device/piperow）：None → ``''``，使模板 guard ``= ''`` 为真；
    - 数值类（row/tube）：None → ``NULL``，使模板 guard ``IS NULL`` 为真；
    - 第一屏/第二屏：展开 piperow_name LIKE 为 OR 别名。
    """
    if not scope_sql_rewrite_enabled():
        return sql, []

    rewritten = sql
    notes: list[str] = []

    for placeholder, scope_key in _SCOPE_STRING_PLACEHOLDERS:
        rewritten, part_notes = _rewrite_string_placeholder(
            rewritten,
            placeholder=placeholder,
            scope_key=scope_key,
            scopes=scopes,
        )
        notes.extend(part_notes)

    piperow_name = _scope_value(scopes, "piperow_name")
    rewritten, alias_notes = _expand_piperow_like_aliases(rewritten, piperow_name)
    notes.extend(alias_notes)

    for placeholder, scope_key in _INT_PLACEHOLDERS:
        rewritten, part_notes = _rewrite_int_placeholder(
            rewritten,
            placeholder=placeholder,
            scope_key=scope_key,
            scopes=scopes,
        )
        notes.extend(part_notes)

    return rewritten, notes
