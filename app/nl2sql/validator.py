from __future__ import annotations

import re
from typing import Iterable


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
    def _canonical_identifier(name: str) -> str:
        t = name.strip().strip("`").strip('"').strip("[]")
        if "." in t:
            t = t.split(".")[-1]
        return t.lower()

