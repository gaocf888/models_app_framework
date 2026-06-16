"""NL2SQL 执行失败结构化异常（供日志、分析 trace 与产品入口映射）。"""

from __future__ import annotations

import re
from typing import Any


def classify_sql_executor_error(exc: BaseException | None) -> str:
    """根据数据库执行器异常粗分类 error_code（不含完整 SQL / 堆栈）。"""
    if exc is None:
        return "sql_exec_failed"
    msg = str(exc).lower()
    if "unknown column" in msg or "(1054," in msg or "1054," in msg:
        return "unknown_column"
    if "unknown table" in msg or "(1146," in msg or "1146," in msg:
        return "unknown_table"
    if "syntax" in msg or "(1064," in msg or "1064," in msg:
        return "sql_syntax_error"
    if "access denied" in msg or "(1045," in msg:
        return "db_access_denied"
    return "sql_exec_failed"


def _one_line_cause(exc: BaseException | None, *, max_len: int = 240) -> str:
    if exc is None:
        return ""
    text = re.sub(r"\s+", " ", str(exc)).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


class NL2SQLExecutionError(Exception):
    """
    SQL 生成后执行失败（含 EXPLAIN / SELECT 路径，refine 用尽后抛出）。

    - ``str(exc)`` / ``brief_message``：短摘要，供分析 trace 与 API detail；
    - ``cause``：原始执行器异常，仅日志使用；
    - ``sql``：失败时最后一次尝试的 SQL，供日志与受控 meta。
    """

    def __init__(
        self,
        *,
        error_code: str,
        sql: str = "",
        cause: BaseException | None = None,
        user_message_key: str = "default",
        parsed_intent: dict[str, Any] | None = None,
    ) -> None:
        self.error_code = error_code
        self.sql = (sql or "").strip()
        self.cause = cause
        self.user_message_key = user_message_key or "default"
        self.parsed_intent = parsed_intent
        self.brief_message = f"NL2SQL {self.error_code}"
        super().__init__(self.brief_message)

    @classmethod
    def from_executor_failure(
        cls,
        *,
        sql: str,
        cause: BaseException | None,
    ) -> NL2SQLExecutionError:
        code = classify_sql_executor_error(cause)
        key = code if code in _USER_MESSAGE_KEYS else "default"
        return cls(error_code=code, sql=sql, cause=cause, user_message_key=key)

    def log_detail(self) -> str:
        """ERROR 日志用：一行 cause 摘要，不含完整 SQL。"""
        cause_line = _one_line_cause(self.cause)
        if cause_line:
            return f"{self.brief_message}; cause={cause_line}"
        return self.brief_message


_USER_MESSAGE_KEYS = frozenset(
    {"unknown_column", "unknown_table", "sql_syntax_error", "db_access_denied", "default"}
)
