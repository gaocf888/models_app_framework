"""chatbot_nl2sql_answer 单元测试。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.graphs.chatbot_nl2sql_answer import (
    format_nl2sql_user_error,
    run_chatbot_nl2sql_query,
    summarize_nl2sql_with_llm,
)
from app.models.nl2sql import NL2SQLQueryResponse
from app.nl2sql.errors import NL2SQLExecutionError


def _cfg(**overrides):
    base = {
        "nl2sql_llm_analysis_enabled": False,
        "nl2sql_empty_llm_guide_enabled": False,
        "nl2sql_analysis_max_rows": 80,
        "nl2sql_analysis_max_tokens": 1024,
        "nl2sql_analysis_timeout_sec": 120.0,
        "nl2sql_analysis_temperature": 0.2,
        "nl2sql_analysis_meta_enabled": True,
    }
    base.update(overrides)
    return type("C", (), base)()


@pytest.mark.asyncio
async def test_summarize_empty_rows_omits_sql_from_user_text():
    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type("A", (), {"chatbot": _cfg()})()
        result = await summarize_nl2sql_with_llm(
            None,
            user_query="换管统计",
            sql="SELECT 1",
            rows=[],
        )
    text = result.answer_text
    assert "查询已执行，当前条件下没有返回数据行" in text
    assert "```sql" not in text
    assert "SELECT 1" not in text
    assert result.analysis_meta is not None
    assert result.analysis_meta.get("empty") is True


@pytest.mark.asyncio
async def test_summarize_with_rows_omits_sql_from_user_text():
    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type("A", (), {"chatbot": _cfg()})()
        result = await summarize_nl2sql_with_llm(
            None,
            user_query="换管统计",
            sql="SELECT 1",
            rows=[{"名称": "A"}],
        )
    text = result.answer_text
    assert "|" in text
    assert "```sql" not in text
    assert "SELECT 1" not in text
    assert result.analysis_meta is not None
    assert result.analysis_meta.get("llm_analysis_used") is False
    assert "名称" in (result.analysis_meta.get("columns") or [])


@pytest.mark.asyncio
async def test_summarize_with_llm_analysis_uses_model_markdown():
    llm = MagicMock()
    llm.chat = AsyncMock(
        return_value=(
            "### 查询总结\n"
            "负荷正常。\n\n"
            "### 明细数据\n"
            "| 锅炉 | 负荷 |\n| --- | --- |\n| 1号 | 100 |\n\n"
            "### 数据业务分析\n"
            "请关注负荷波动。"
        )
    )
    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type(
            "A",
            (),
            {"chatbot": _cfg(nl2sql_llm_analysis_enabled=True)},
        )()
        with patch(
            "app.llm.graphs.chatbot_nl2sql_answer._load_scene_system",
            return_value="sys",
        ):
            result = await summarize_nl2sql_with_llm(
                llm,
                user_query="本厂1号锅炉当前负荷是多少",
                sql="SELECT load FROM t",
                rows=[{"锅炉": "1号", "负荷": 100}],
            )
    assert "负荷正常" in result.answer_text
    assert "请关注负荷波动" in result.answer_text
    assert "### 查询总结" not in result.answer_text
    assert "### 明细数据" not in result.answer_text
    assert "### 数据业务分析" not in result.answer_text
    assert result.analysis_meta is not None
    assert result.analysis_meta.get("llm_analysis_used") is True
    llm.chat.assert_awaited()


def test_strip_nl2sql_analysis_section_headings():
    from app.llm.graphs.chatbot_nl2sql_answer import strip_nl2sql_analysis_section_headings

    raw = (
        "### 概括\n"
        "已查到1号锅炉负荷421MW。\n\n"
        "### 精简 Markdown 表\n"
        "| 锅炉 | 负荷 |\n| --- | --- |\n| 1号 | 421 |\n\n"
        "### 业务解读\n"
        "处于高负荷区间，建议关注燃烧稳定性。"
    )
    out = strip_nl2sql_analysis_section_headings(raw)
    assert "已查到1号锅炉负荷421MW" in out
    assert "| 1号 | 421 |" in out
    assert "处于高负荷区间" in out
    assert "概括" not in out
    assert "业务解读" not in out
    assert "精简 Markdown 表" not in out
    assert "###" not in out


def test_ensure_nl2sql_partial_rows_note_inserts_after_table():
    from app.llm.graphs.chatbot_nl2sql_answer import (
        ensure_nl2sql_partial_rows_note,
        finalize_streamed_nl2sql_analysis,
        Nl2sqlAnalysisStreamPlan,
    )

    raw = (
        "2号锅炉昨日共发生91次超温。\n\n"
        "| 机组 | 测点 |\n| --- | --- |\n"
        "| 2号 | A |\n| 2号 | B |\n\n"
        "建议关注水冷壁螺旋管段测点。"
    )
    out = ensure_nl2sql_partial_rows_note(raw, total_row_count=91, shown_row_count=2)
    assert "> 共 91 条，上表仅展示其中 2 条代表性明细。" in out
    assert out.index("| 2号 | B |") < out.index("共 91 条")
    assert out.index("共 91 条") < out.index("建议关注")

    # 已有提示不重复
    once = ensure_nl2sql_partial_rows_note(out, total_row_count=91, shown_row_count=2)
    assert once.count("共 91 条") == 1

    plan = Nl2sqlAnalysisStreamPlan(
        system="sys",
        user_content="user",
        table_fallback="| a | b |\n| --- | --- |\n| 1 | 2 |",
        display_rows=[{"机组": "2号"}, {"机组": "2号"}],
        total_row_count=91,
        analysis_prompt_row_count=2,
        sql="SELECT 1",
        user_query="查超温",
    )
    finalized = finalize_streamed_nl2sql_analysis(
        plan,
        "### 概括\n昨日共91次超温，其中前20条明细数据已展示。\n\n"
        "### 精简 Markdown 表\n"
        "| 机组 | 测点 |\n| --- | --- |\n| 2号 | A |\n| 2号 | B |\n\n"
        "### 业务解读\n请现场核对。",
    )
    assert "### 概括" not in finalized.answer_text
    assert "### 业务解读" not in finalized.answer_text
    assert "精简 Markdown 表" not in finalized.answer_text
    assert "前20条" not in finalized.answer_text
    assert "前2条明细已展示" in finalized.answer_text
    assert "> 共 91 条，上表仅展示其中 2 条代表性明细。" in finalized.answer_text


@pytest.mark.asyncio
async def test_summarize_empty_with_llm_guide():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="本次无数据，建议缩小时间范围。")
    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type(
            "A",
            (),
            {"chatbot": _cfg(nl2sql_empty_llm_guide_enabled=True)},
        )()
        with patch(
            "app.llm.graphs.chatbot_nl2sql_answer._load_scene_system",
            return_value="sys",
        ):
            result = await summarize_nl2sql_with_llm(
                llm,
                user_query="查台账",
                sql="SELECT 1",
                rows=[],
            )
    assert "缩小时间范围" in result.answer_text
    assert result.analysis_meta and result.analysis_meta.get("llm_analysis_used") is True


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
    assert outcome.nl2sql_analysis is None


@pytest.mark.asyncio
async def test_run_chatbot_nl2sql_query_gen_failed_keeps_hitl_contract() -> None:
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(
        return_value=NL2SQLQueryResponse(sql="", rows=[], gen_fail_reason="empty_sql")
    )
    outcome = await run_chatbot_nl2sql_query(
        nl2sql,
        None,
        user_id="u1",
        session_id="s1",
        question="乱问",
    )
    assert outcome.gen_failed is True
    assert outcome.answer_text == ""
    assert outcome.terminate_reason == "nl2sql_gen_failed"
    assert outcome.nl2sql_analysis is None


@pytest.mark.asyncio
async def test_analysis_meta_serializes_decimal_and_datetime():
    from datetime import datetime
    from decimal import Decimal
    import json

    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type("A", (), {"chatbot": _cfg()})()
        result = await summarize_nl2sql_with_llm(
            None,
            user_query="超温",
            sql="SELECT 1",
            rows=[
                {
                    "最高壁温_℃": Decimal("512.30"),
                    "超温时长_秒": Decimal("120"),
                    "超温开始时间": datetime(2026, 7, 16, 10, 0, 0),
                }
            ],
        )
    assert result.analysis_meta is not None
    # finished.meta 必须可被标准 json.dumps 序列化
    payload = json.dumps({"finished": True, "meta": {"nl2sql_analysis": result.analysis_meta}}, ensure_ascii=False)
    assert "512.3" in payload or "512.30" in payload
    assert "2026-07-16" in payload


@pytest.mark.asyncio
async def test_llm_prompt_uses_capped_rows_not_full_json_dump():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="## 结论\n样本内最高壁温偏高。")
    rows = [{"机组": f"r{i}", "负荷": i} for i in range(60)]
    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type(
            "A",
            (),
            {"chatbot": _cfg(nl2sql_llm_analysis_enabled=True, nl2sql_analysis_max_rows=80)},
        )()
        with patch(
            "app.llm.graphs.chatbot_nl2sql_answer._load_scene_system",
            return_value="sys",
        ):
            result = await summarize_nl2sql_with_llm(
                llm,
                user_query="超温明细",
                sql="SELECT 1",
                rows=rows,
            )
    assert result.analysis_meta and result.analysis_meta.get("llm_analysis_used") is True
    call_kwargs = llm.chat.await_args.kwargs
    user_msg = call_kwargs["messages"][1]["content"]
    assert "JSON" not in user_msg
    assert "前 12 行" in user_msg or "12 行" in user_msg
    assert len(user_msg) < 20000
    assert call_kwargs.get("timeout") == 120.0
    assert call_kwargs.get("max_tokens") == 1024


@pytest.mark.asyncio
async def test_llm_prompt_slims_wide_columns():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="## 结论\n已整理。")
    wide = {
        "机组名称": "1号",
        "测点名称": "P1",
        "排号": 1,
        "管号": 2,
        "设备名称": "屏",
        "设备描述": "desc",
        "管屏名称": "A",
        "横向间距": 1,
        "纵向间距": 2,
        "排数": 3,
        "管数": 4,
        "管屏型号": "M",
        "管屏直径": 5,
        "超温开始时间": "2026-07-16 10:00:00",
        "最高壁温_℃": 512,
        "限值_℃": 480,
        "超温时长_秒": 120,
        "负荷_MW": 300,
        "无关列X": "x",
        "无关列Y": "y",
        "无关列Z": "z",
    }
    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type(
            "A",
            (),
            {"chatbot": _cfg(nl2sql_llm_analysis_enabled=True)},
        )()
        with patch(
            "app.llm.graphs.chatbot_nl2sql_answer._load_scene_system",
            return_value="sys",
        ):
            await summarize_nl2sql_with_llm(
                llm,
                user_query="超温明细",
                sql="SELECT 1",
                rows=[wide],
            )
    user_msg = llm.chat.await_args.kwargs["messages"][1]["content"]
    assert "收窄" in user_msg
    assert "最高壁温" in user_msg
    assert "无关列Z" not in user_msg


@pytest.mark.asyncio
async def test_defer_analysis_stream_returns_plan_without_llm():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="should-not-call")
    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type(
            "A",
            (),
            {"chatbot": _cfg(nl2sql_llm_analysis_enabled=True)},
        )()
        with patch(
            "app.llm.graphs.chatbot_nl2sql_answer._load_scene_system",
            return_value="sys",
        ):
            result = await summarize_nl2sql_with_llm(
                llm,
                user_query="超温明细",
                sql="SELECT 1",
                rows=[{"最高壁温_℃": 500, "限值_℃": 480}],
                defer_analysis_stream=True,
            )
    assert result.answer_text == ""
    assert result.stream_plan is not None
    assert result.stream_plan.table_fallback
    assert "sys" == result.stream_plan.system
    llm.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_iter_analysis_llm_deltas_streams_chunks():
    from app.llm.graphs.chatbot_nl2sql_answer import iter_analysis_llm_deltas

    class _Client:
        async def stream_chat(self, model=None, messages=None, **kwargs):
            assert kwargs.get("timeout") == 120.0
            yield "## "
            yield "结论"

    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type("A", (), {"chatbot": _cfg()})()
        parts = [
            d
            async for d in iter_analysis_llm_deltas(
                _Client(), system="s", user_content="u"
            )
        ]
    assert parts == ["## ", "结论"]


@pytest.mark.asyncio
async def test_finalize_stream_fallback_to_table():
    from app.llm.graphs.chatbot_nl2sql_answer import (
        Nl2sqlAnalysisStreamPlan,
        finalize_streamed_nl2sql_analysis,
    )

    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type("A", (), {"chatbot": _cfg()})()
        plan = Nl2sqlAnalysisStreamPlan(
            system="s",
            user_content="u",
            table_fallback="| a |\n| --- |\n| 1 |",
            display_rows=[{"a": 1}],
            total_row_count=1,
            sql="SELECT 1",
            user_query="q",
        )
        result = finalize_streamed_nl2sql_analysis(plan, "")
    assert "|" in result.answer_text
    assert result.analysis_meta and result.analysis_meta.get("llm_analysis_used") is False

    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(return_value=NL2SQLQueryResponse(sql="SELECT 1", rows=[{"v": 1}]))
    with patch("app.llm.graphs.chatbot_nl2sql_answer.get_app_config") as gac:
        gac.return_value = type("A", (), {"chatbot": _cfg()})()
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
    assert outcome.nl2sql_analysis is not None
