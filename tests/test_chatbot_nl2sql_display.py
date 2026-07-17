"""智能客服 NL2SQL 展示列过滤单测。"""

from __future__ import annotations

import pytest

from app.llm.graphs.chatbot_nl2sql_answer import summarize_nl2sql_with_llm
from app.llm.graphs.chatbot_nl2sql_display import (
    filter_chatbot_nl2sql_display_rows,
    is_code_dimension_column_name,
    is_force_keep_display_column,
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


def test_code_dimension_and_force_keep_names():
    assert is_code_dimension_column_name("检修状态")
    assert is_code_dimension_column_name("检修等级")
    assert is_code_dimension_column_name("status")
    assert is_code_dimension_column_name("overhaul_level")
    assert is_force_keep_display_column("检修年份")
    assert is_force_keep_display_column("开始日期")
    assert not is_code_dimension_column_name("检修年份")


def test_hide_uuid_like_column_by_values():
    vals = [
        "21477de7aa7e11f0a8600242ac110002",
        "a2477de7aa7e11f0a8600242ac110003",
        "b3477de7aa7e11f0a8600242ac110004",
    ]
    assert should_hide_chatbot_nl2sql_column("some_col", vals)


def test_hide_status_and_level_when_all_short_codes():
    assert should_hide_chatbot_nl2sql_column("检修状态", [0, 0, 0, 0])
    assert should_hide_chatbot_nl2sql_column("检修等级", ["C", "A", "L", "C"])
    # 可读中文状态则保留
    assert not should_hide_chatbot_nl2sql_column(
        "检修状态", ["未开始", "进行中", "已完成", "未开始"]
    )


def test_keep_year_even_if_numeric():
    assert not should_hide_chatbot_nl2sql_column("检修年份", [2025, 2024, 2023, 2022])


def test_filter_rows_drops_id_status_level_keeps_names_dates():
    rows = [
        {
            "检修计划ID": "21477de7aa7e11f0a8600242ac110002",
            "锅炉名称": "2号锅炉",
            "检修名称": "2025年2号炉机器人C修",
            "检修等级": "C",
            "检修开始日期": "2025-09-11",
            "检修结束日期": "2025-10-28",
            "检修状态": 0,
            "检修年份": 2025,
        },
        {
            "检修计划ID": "a2477de7aa7e11f0a8600242ac110003",
            "锅炉名称": "1号锅炉",
            "检修名称": "2025年1号炉A修",
            "检修等级": "A",
            "检修开始日期": "2025-03-01",
            "检修结束日期": "2025-04-01",
            "检修状态": 0,
            "检修年份": 2025,
        },
    ]
    out = filter_chatbot_nl2sql_display_rows(rows)
    assert "检修计划ID" not in out[0]
    assert "检修状态" not in out[0]
    assert "检修等级" not in out[0]
    assert out[0]["锅炉名称"] == "2号锅炉"
    assert out[0]["检修名称"].startswith("2025")
    assert out[0]["检修开始日期"] == "2025-09-11"
    assert out[0]["检修年份"] == 2025


@pytest.mark.asyncio
async def test_summarize_hides_technical_id_and_short_code_cols():
    from unittest.mock import patch

    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type(
            "A",
            (),
            {
                "chatbot": type(
                    "C",
                    (),
                    {
                        "nl2sql_llm_analysis_enabled": False,
                        "nl2sql_empty_llm_guide_enabled": False,
                        "nl2sql_analysis_max_rows": 80,
                        "nl2sql_analysis_max_tokens": 2048,
                        "nl2sql_analysis_temperature": 0.2,
                        "nl2sql_analysis_meta_enabled": False,
                    },
                )()
            },
        )()
        result = await summarize_nl2sql_with_llm(
            None,
            user_query="查检修计划",
            sql="SELECT id, name FROM t",
            rows=[
                {
                    "id": "21477de7aa7e11f0a8600242ac110002",
                    "锅炉名称": "1号",
                    "检修等级": "C",
                    "检修状态": 0,
                },
                {
                    "id": "a2477de7aa7e11f0a8600242ac110003",
                    "锅炉名称": "2号",
                    "检修等级": "A",
                    "检修状态": 0,
                },
            ],
        )
    text = result.answer_text
    assert "锅炉名称" in text
    assert "21477de7" not in text
    assert "检修等级" not in text
    assert "检修状态" not in text
