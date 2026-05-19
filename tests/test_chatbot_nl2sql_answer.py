"""chatbot_nl2sql_answer 单元测试。"""

import pytest

from app.llm.graphs.chatbot_nl2sql_answer import summarize_nl2sql_with_llm


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
