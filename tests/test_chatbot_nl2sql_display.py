"""智能客服 NL2SQL 展示列过滤单测。"""

from __future__ import annotations

import pytest

from app.llm.graphs.chatbot_nl2sql_answer import summarize_nl2sql_with_llm
from app.llm.graphs.chatbot_nl2sql_display import (
    filter_chatbot_nl2sql_display_rows,
    is_technical_id_column_name,
    should_hide_chatbot_nl2sql_column,
)


def test_hide_id_and_fk_column_names():
    assert is_technical_id_column_name("id")
    assert is_technical_id_column_name("boiler_id")
    assert is_technical_id_column_name("检修计划ID")
    assert not is_technical_id_column_name("锅炉名称")
    assert not is_technical_id_column_name("status")
    assert not is_technical_id_column_name("overhaul_level")


def test_hide_uuid_like_column_by_values():
    vals = [
        "21477de7aa7e11f0a8600242ac110002",
        "a2477de7aa7e11f0a8600242ac110003",
        "b3477de7aa7e11f0a8600242ac110004",
    ]
    assert should_hide_chatbot_nl2sql_column("some_col", vals)


def test_filter_rows_drops_plan_id_keeps_business_cols():
    rows = [
        {
            "检修计划ID": "21477de7aa7e11f0a8600242ac110002",
            "锅炉名称": "2号锅炉",
            "检修等级": "C",
            "检修状态": 0,
        },
        {
            "检修计划ID": "a2477de7aa7e11f0a8600242ac110003",
            "锅炉名称": "1号锅炉",
            "检修等级": "A",
            "检修状态": 0,
        },
    ]
    out = filter_chatbot_nl2sql_display_rows(rows)
    assert "检修计划ID" not in out[0]
    assert out[0]["锅炉名称"] == "2号锅炉"
    assert out[0]["检修等级"] == "C"
    assert out[0]["检修状态"] == 0


@pytest.mark.asyncio
async def test_summarize_hides_technical_id_column():
    text = await summarize_nl2sql_with_llm(
        None,
        user_query="查检修计划",
        sql="SELECT id, name FROM t",
        rows=[
            {"id": "21477de7aa7e11f0a8600242ac110002", "锅炉名称": "1号", "检修等级": "C"},
            {"id": "a2477de7aa7e11f0a8600242ac110003", "锅炉名称": "2号", "检修等级": "A"},
        ],
    )
    assert "锅炉名称" in text
    assert "检修等级" in text
    assert "21477de7" not in text
    assert "| id |" not in text.lower() or "id" not in text.split("\n")[0].lower()
