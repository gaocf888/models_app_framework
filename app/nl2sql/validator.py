from __future__ import annotations

import re


class SQLValidator:
    """
    NL2SQL 生成 SQL 的基础校验器（骨架版）。

    - 确保只包含 SELECT 语句；
    - 粗略拦截 UPDATE/DELETE/INSERT/DDL。
    """

    _forbidden_pattern = re.compile(r"\b(UPDATE|DELETE|INSERT|DROP|ALTER|TRUNCATE)\b", re.IGNORECASE)

    def validate(self, sql: str) -> bool:
        s = sql.strip().rstrip(";")
        if not s.lower().startswith("select"):
            return False
        if self._forbidden_pattern.search(s):
            return False
        return True

