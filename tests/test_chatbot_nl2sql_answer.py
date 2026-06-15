"""chatbot_nl2sql_answer 单元测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.llm.graphs.chatbot_nl2sql_answer import (
    format_nl2sql_user_error,
    run_chatbot_nl2sql_query,
    summarize_nl2sql_with_llm,
)
from app.models.nl2sql import NL2SQLQueryResponse
from app.nl2sql.errors import NL2SQLExecutionError


@pytest.mark.asyncio
async def test_summarize_empty_rows_omits_sql_from_user_text():
    text = await summarize_nl2sql_with_llm(
        None,
        user_query="换管统计",
        sql="SELECT 1",
        rows=[],
    )
    assert "查询已执行，当前条件下没有返回数据行" in text
    assert "```sql" not in text
    assert "SELECT 1" not in text


@pytest.mark.asyncio
async def test_summarize_with_rows_omits_sql_from_user_text():
    text = await summarize_nl2sql_with_llm(
        None,
        user_query="换管统计",
        sql="SELECT 1",
        rows=[{"名称": "A"}],
    )
    assert "|" in text
    assert "```sql" not in text
    assert "SELECT 1" not in text


def test_format_nl2sql_user_error_hides_technical_detail() -> None:
    err = NL2SQLExecutionError.from_executor_failure(
        sql="SELECT bad",
        cause=RuntimeError("(1054, \"Unknown column 'x'\")"),
    )
    text = format_nl2sql_user_error(err)
    assert "暂时无法完成本次数据查询" in text
    assert "1054" not in text
    assert "SELECT" not in text


@pytest.mark.asyncio
async def test_run_chatbot_nl2sql_query_returns_friendly_text_on_execution_error() -> None:
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(
        side_effect=NL2SQLExecutionError.from_executor_failure(
            sql="SELECT asd.x",
            cause=RuntimeError("(1054, \"Unknown column 'asd.x'\")"),
        )
    )
    outcome = await run_chatbot_nl2sql_query(
        nl2sql,
        None,
        user_id="u1",
        session_id="s1",
        question="1号锅炉超温统计",
    )
    assert outcome.nl2sql_failed is True
    assert outcome.nl2sql_error_code == "unknown_column"
    assert outcome.terminate_reason == "nl2sql_exec_failed"
    assert "暂时无法完成本次数据查询" in outcome.answer_text
    assert "asd" not in outcome.answer_text
    assert outcome.nl2sql_sql is None


@pytest.mark.asyncio
async def test_run_chatbot_nl2sql_query_success_path() -> None:
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(return_value=NL2SQLQueryResponse(sql="SELECT 1", rows=[{"v": 1}]))
    outcome = await run_chatbot_nl2sql_query(
        nl2sql,
        None,
        user_id="u1",
        session_id="s1",
        question="统计",
    )
    assert outcome.nl2sql_failed is False
    assert "|" in outcome.answer_text
    assert outcome.nl2sql_sql == "SELECT 1"
