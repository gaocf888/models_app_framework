from __future__ import annotations

import re
from typing import Iterable


def _starts_sql_word(s: str, i: int, word: str) -> bool:
    n = len(word)
    if i + n > len(s):
        return False
    if s[i : i + n].lower() != word:
        return False
    before = s[i - 1] if i > 0 else " "
    la = s[i + n] if i + n < len(s) else " "
    if (before.isalnum() or before == "_") or (la.isalnum() or la == "_"):
        return False
    return True


def _find_matching_paren(sql: str, open_idx: int) -> int:
    """open_idx 指向 '('，返回匹配的 ')' 下标，失败返回 -1。"""
    if open_idx >= len(sql) or sql[open_idx] != "(":
        return -1
    depth = 0
    quote: str | None = None
    i = open_idx
    n = len(sql)
    while i < n:
        ch = sql[i]
        if quote == "'":
            if ch == "'" and (i + 1 < n and sql[i + 1] == "'"):
                i += 2
                continue
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote in ('"', "`"):
            if ch == quote and (i + 1 < n and sql[i + 1] == quote):
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            continue
        if quote:
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            if depth == 0:
                return i - 1
            continue
        i += 1
    return -1


def _extract_main_from_clause(normalized_sql: str) -> str | None:
    """取最外层 SELECT 对应的 FROM 子句片段（不含 FROM 关键字），失败返回 None。"""
    s = (normalized_sql or "").strip()
    if not s:
        return None
    n = len(s)
    i = 0
    quote: str | None = None
    depth = 0
    from_pos: int | None = None
    while i < n:
        ch = s[i]
        if quote == "'":
            if ch == "'" and (i + 1 < n and s[i + 1] == "'"):
                i += 2
                continue
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote in ('"', "`"):
            if ch == quote and (i + 1 < n and s[i + 1] == quote):
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            continue
        if quote:
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and _starts_sql_word(s, i, "from"):
            from_pos = i + 4
            while from_pos < n and s[from_pos].isspace():
                from_pos += 1
            break
        i += 1
    if from_pos is None:
        return None
    i = from_pos
    quote = None
    depth = 0
    segment_start = i
    while i < n:
        ch = s[i]
        if quote == "'":
            if ch == "'" and (i + 1 < n and s[i + 1] == "'"):
                i += 2
                continue
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote in ('"', "`"):
            if ch == quote and (i + 1 < n and s[i + 1] == quote):
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            continue
        if quote:
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            for kw in ("where", "group", "order", "having", "limit", "union", "window"):
                if _starts_sql_word(s, i, kw):
                    return s[segment_start:i].strip()
        i += 1
    return s[segment_start:].strip()


def _split_from_clause_atoms(from_clause: str) -> list[str]:
    """将 FROM 子句拆成表项（逗号 / JOIN 分隔），不含 ON 之后内容。"""
    fc = (from_clause or "").strip()
    if not fc:
        return []
    chunks: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    n = len(fc)

    def flush() -> None:
        t = "".join(buf).strip()
        if t:
            chunks.append(t)
        buf.clear()

    while i < n:
        ch = fc[i]
        if quote == "'":
            buf.append(ch)
            if ch == "'" and (i + 1 < n and fc[i + 1] == "'"):
                buf.append("'")
                i += 2
                continue
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote in ('"', "`"):
            buf.append(ch)
            if ch == quote and (i + 1 < n and fc[i + 1] == quote):
                buf.append(ch)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if quote:
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            i += 1
            continue
        if depth == 0 and ch == ",":
            flush()
            i += 1
            continue
        if depth == 0 and _starts_sql_word(fc, i, "join"):
            flush()
            i += 4
            while i < n and fc[i].isspace():
                i += 1
            for w in ("inner", "outer", "left", "right", "cross", "full"):
                lw = len(w)
                if i + lw <= n and _starts_sql_word(fc, i, w):
                    i += lw
                    while i < n and fc[i].isspace():
                        i += 1
            continue
        buf.append(ch)
        i += 1
    flush()
    out: list[str] = []
    for chunk in chunks:
        atom = _strip_trailing_on_clause(chunk)
        if atom:
            out.append(atom)
    return out


def _strip_trailing_on_clause(atom: str) -> str:
    s = atom.strip()
    if not s:
        return ""
    depth = 0
    quote: str | None = None
    i = 0
    n = len(s)
    on_at: int | None = None
    while i < n:
        ch = s[i]
        if quote == "'":
            if ch == "'" and (i + 1 < n and s[i + 1] == "'"):
                i += 2
                continue
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote in ('"', "`"):
            if ch == quote and (i + 1 < n and s[i + 1] == quote):
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            i += 1
            continue
        if quote:
            i += 1
            continue
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0 and _starts_sql_word(s, i, "on"):
            on_at = i
            break
        i += 1
    if on_at is not None:
        return s[:on_at].strip()
    return s


def _clean_ident(tok: str) -> str:
    t = tok.strip().strip("`").strip('"').strip("[]")
    if "." in t:
        t = t.split(".")[-1]
    return t.lower()


def _parse_table_alias_atom(atom: str) -> tuple[str | None, str | None]:
    """
    解析单个 FROM 项，返回 (物理表名小写, 别名小写)。
    子查询返回 (None, alias) 或 (None, None)。
    """
    s = atom.strip()
    if not s:
        return None, None
    if s.startswith("("):
        end = _find_matching_paren(s, 0)
        if end < 0:
            return None, None
        rest = s[end + 1 :].strip()
        if not rest:
            return None, None
        alias = rest.split()[0]
        return None, _clean_ident(alias)
    parts = s.split()
    if not parts:
        return None, None
    join_like = {"inner", "outer", "left", "right", "cross", "full", "straight_join"}
    while parts and parts[0].lower() in join_like:
        parts = parts[1:]
    if not parts:
        return None, None
    upper = [p.upper() for p in parts]
    if len(parts) >= 3 and upper[-2] == "AS":
        table_raw = parts[-3]
        alias_raw = parts[-1]
    elif len(parts) >= 2:
        table_raw = parts[0]
        alias_raw = parts[1]
        if upper[1] in ("ON", "USING"):
            alias_raw = parts[0].split(".")[-1] if "." in parts[0] else parts[0]
            return _clean_ident(table_raw), _clean_ident(alias_raw)
    else:
        table_raw = parts[0]
        alias_raw = parts[0]
    tab = _clean_ident(table_raw)
    als = _clean_ident(alias_raw)
    if not tab:
        return None, None
    if not als:
        als = tab
    return tab, als


class SQLValidator:
    """
    NL2SQL 生成 SQL 的基础校验器（骨架版）。

    - 确保只包含 SELECT 语句；
    - 粗略拦截 UPDATE/DELETE/INSERT/DDL。
    """

    _forbidden_pattern = re.compile(r"\b(UPDATE|DELETE|INSERT|DROP|ALTER|TRUNCATE)\b", re.IGNORECASE)
    _fenced_sql_pattern = re.compile(r"```(?:\w+)?\s*(.*?)\s*```", re.IGNORECASE | re.DOTALL)
    _table_ref_pattern = re.compile(r"\b(?:FROM|JOIN)\s+([`\"\[]?[a-zA-Z_][\w\.]*[`\"\]]?)", re.IGNORECASE)
    _qualified_col_pattern = re.compile(r"\b[a-zA-Z_][\w]*\.([a-zA-Z_][\w]*)\b")
    _keyword_blocklist = {
        "select",
        "from",
        "where",
        "join",
        "inner",
        "left",
        "right",
        "full",
        "on",
        "and",
        "or",
        "as",
        "group",
        "order",
        "by",
        "limit",
        "having",
        "union",
        "all",
        "distinct",
        "case",
        "when",
        "then",
        "else",
        "end",
        "with",
    }

    @classmethod
    def normalize_sql(cls, sql: str) -> str:
        """
        归一化 LLM 返回的 SQL 文本，兼容 markdown code block 等包裹格式。
        """
        s = (sql or "").strip()
        if not s:
            return ""

        # 兼容 ```sql ... ``` 包裹，避免校验/执行阶段误判。
        fenced = cls._fenced_sql_pattern.search(s)
        if fenced:
            s = fenced.group(1).strip()

        # 兼容 `sql\nSELECT ...` 这类前缀输出。
        if s.lower().startswith("sql\n"):
            s = s[4:].strip()

        s = cls._collapse_whitespace_preserve_quoted(s)
        return s.rstrip(";").strip()

    @classmethod
    def _collapse_whitespace_preserve_quoted(cls, sql: str) -> str:
        """
        将 SQL 中「引号外」的连续空白（含换行、制表符）压成单个空格，得到紧凑单行语句；
        字符串字面量与引号包裹的标识符内部原样保留（含单引号 ''、双引号 ""、反引号 `` 转义）。
        """
        out: list[str] = []
        i = 0
        n = len(sql)
        quote: str | None = None

        while i < n:
            ch = sql[i]

            if quote == "'":
                out.append(ch)
                if ch == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        out.append("'")
                        i += 2
                        continue
                    quote = None
                i += 1
                continue

            if quote == '"':
                out.append(ch)
                if ch == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        out.append('"')
                        i += 2
                        continue
                    quote = None
                i += 1
                continue

            if quote == "`":
                out.append(ch)
                if ch == "`":
                    if i + 1 < n and sql[i + 1] == "`":
                        out.append("`")
                        i += 2
                        continue
                    quote = None
                i += 1
                continue

            if ch in ("'", '"', "`"):
                quote = ch
                out.append(ch)
                i += 1
                continue

            if ch.isspace():
                j = i
                while j < n and sql[j].isspace():
                    j += 1
                if out and out[-1] != " ":
                    out.append(" ")
                i = j
                continue

            out.append(ch)
            i += 1

        return "".join(out).strip()

    def validate(self, sql: str) -> bool:
        s = self.normalize_sql(sql)
        if not s:
            return False
        if not (s.lower().startswith("select") or s.lower().startswith("with")):
            return False
        if self._forbidden_pattern.search(s):
            return False
        return True

    @classmethod
    def extract_identifiers_from_snippets(cls, snippets: Iterable[str]) -> tuple[set[str], set[str]]:
        """
        从 RAG 片段中提取候选表名/字段名，用于白名单校验。
        """
        text = "\n".join(snippets)
        tables: set[str] = set()
        columns: set[str] = set()

        for m in re.finditer(r"[（(]\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*[）)]", text):
            token = m.group(1).lower()
            if token not in cls._keyword_blocklist:
                tables.add(token)

        for m in re.finditer(r"\b(?:table|表名|数据库表名)\s*[:：]?\s*([a-zA-Z_][a-zA-Z0-9_]*)\b", text, re.IGNORECASE):
            token = m.group(1).lower()
            if token not in cls._keyword_blocklist:
                tables.add(token)

        for m in re.finditer(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\b", text):
            token = m.group(1).lower()
            if token in cls._keyword_blocklist:
                continue
            if "_" in token and len(token) >= 4:
                columns.add(token)

        columns -= tables
        return tables, columns

    def validate_identifiers(
        self,
        sql: str,
        *,
        allowed_tables: set[str] | None = None,
        allowed_columns: set[str] | None = None,
    ) -> tuple[bool, str | None]:
        """
        对 SQL 中出现的表/字段做白名单校验。
        """
        s = self.normalize_sql(sql)
        if not s:
            return False, "empty sql"

        tables = {self._canonical_identifier(t) for t in self._table_ref_pattern.findall(s)}
        tables = {t for t in tables if t}
        if allowed_tables and tables:
            unknown_tables = sorted(t for t in tables if t.lower() not in allowed_tables)
            if unknown_tables:
                return False, f"unknown tables: {', '.join(unknown_tables)}"

        qualified_cols = {c.lower() for c in self._qualified_col_pattern.findall(s)}
        if allowed_columns and qualified_cols:
            unknown_cols = sorted(c for c in qualified_cols if c not in allowed_columns)
            if unknown_cols:
                return False, f"unknown columns: {', '.join(unknown_cols)}"

        return True, None

    @staticmethod
    def _match_index_outside_string_literals(sql: str, pos: int) -> bool:
        """pos 为标识符起点时，若落在字符串/反引号标识符字面量内则返回 False。"""
        if pos < 0 or pos > len(sql):
            return False
        i = 0
        n = len(sql)
        quote: str | None = None
        while i < pos:
            ch = sql[i]
            if quote == "'":
                if ch == "'" and (i + 1 < n and sql[i + 1] == "'"):
                    i += 2
                    continue
                if ch == "'":
                    quote = None
                i += 1
                continue
            if quote in ('"', "`"):
                if ch == quote and (i + 1 < n and sql[i + 1] == quote):
                    i += 2
                    continue
                if ch == quote:
                    quote = None
                i += 1
                continue
            if ch in ("'", '"', "`"):
                quote = ch
            i += 1
        return quote is None

    @classmethod
    def parse_table_aliases_from_sql(cls, sql: str) -> dict[str, str]:
        """
        解析主查询 FROM 子句中的「别名 -> 物理表名」（小写）。
        子查询别名不映射表名，后续列绑定校验会跳过这些别名上的限定列。
        """
        s = cls.normalize_sql(sql)
        if not s:
            return {}
        fc = _extract_main_from_clause(s)
        if not fc:
            return {}
        alias_map: dict[str, str] = {}
        for atom in _split_from_clause_atoms(fc):
            tab, als = _parse_table_alias_atom(atom)
            if tab and als:
                alias_map[als] = tab
        return alias_map

    @classmethod
    def validate_column_table_binding(
        cls,
        sql: str,
        *,
        table_columns: dict[str, set[str]] | None,
    ) -> tuple[bool, str | None]:
        """
        校验 alias.column / table.column 中的列是否属于对应物理表（需 DB 反射得到的 table_columns）。
        """
        if not table_columns:
            return True, None
        s = cls.normalize_sql(sql)
        if not s:
            return True, None
        alias_map = cls.parse_table_aliases_from_sql(s)
        pat = re.compile(r"\b([a-zA-Z_][\w]*)\.([a-zA-Z_][\w]*)\b")
        bad: list[str] = []
        for m in pat.finditer(s):
            if not cls._match_index_outside_string_literals(s, m.start()):
                continue
            left, right = m.group(1).lower(), m.group(2).lower()
            if left in cls._keyword_blocklist:
                continue
            tbl: str | None = None
            if left in alias_map:
                tbl = alias_map[left]
            elif left in table_columns:
                tbl = left
            else:
                continue
            cols = table_columns.get(tbl)
            if not cols:
                continue
            if right not in cols:
                bad.append(f"{left}.{right} (table {tbl} has no column {right})")
        if bad:
            return False, "column-table binding failed: " + "; ".join(bad[:6])
        return True, None

    @staticmethod
    def _canonical_identifier(name: str) -> str:
        t = name.strip().strip("`").strip('"').strip("[]")
        if "." in t:
            t = t.split(".")[-1]
        return t.lower()

