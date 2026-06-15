"""NL2SQL 结构化执行异常单元测试。"""

import pytest

from app.nl2sql.errors import NL2SQLExecutionError, classify_sql_executor_error


def test_classify_unknown_column() -> None:
    exc = RuntimeError("(1054, \"Unknown column 'asd.device_name' in 'field list'\")")
    assert classify_sql_executor_error(exc) == "unknown_column"


def test_classify_generic() -> None:
    assert classify_sql_executor_error(RuntimeError("connection reset")) == "sql_exec_failed"


def test_execution_error_brief_message_without_sql() -> None:
    err = NL2SQLExecutionError.from_executor_failure(
        sql="SELECT asd.device_name FROM t d",
        cause=RuntimeError("(1054, \"Unknown column 'asd.device_name'\")"),
    )
    assert err.error_code == "unknown_column"
    assert err.sql == "SELECT asd.device_name FROM t d"
    assert "asd.device_name" not in str(err)
    assert "1054" not in str(err)
    assert err.brief_message == "NL2SQL unknown_column"


def test_log_detail_includes_truncated_cause() -> None:
    err = NL2SQLExecutionError.from_executor_failure(
        sql="SELECT 1",
        cause=RuntimeError("boom"),
    )
    assert "cause=boom" in err.log_detail()
