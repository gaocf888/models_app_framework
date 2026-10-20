"""智能问答沉降查数：锁库 / 运维分流 / period 短路准备。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.nl2sql.scope_station_promote import promote_station_ids


def test_promote_station_ids_short_code_to_full_name() -> None:
    sid, sname = promote_station_ids("F2", "F2(望京)")
    assert sid == "F2(望京)"
    assert sname == "F2(望京)"


def test_promote_station_ids_keeps_full_id() -> None:
    sid, sname = promote_station_ids("F2(望京)", "F2(望京)")
    assert sid == "F2(望京)"


@pytest.mark.asyncio
async def test_prepare_ops_skips_forced_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    from app.llm.graphs.chatbot_business_profile import clear_chatbot_business_profile_cache
    from app.nl2sql.chatbot_settle_prepare import prepare_chatbot_settle_query

    clear_chatbot_business_profile_cache()
    prep = prepare_chatbot_settle_query("当前各仪器类型在线率是多少")
    assert prep.ops_routed is True
    assert prep.forced_tables is None
    assert prep.period_eligible is False


@pytest.mark.asyncio
async def test_prepare_settle_defaults_fcb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    from app.llm.graphs.chatbot_business_profile import clear_chatbot_business_profile_cache
    from app.nl2sql.chatbot_settle_prepare import prepare_chatbot_settle_query

    clear_chatbot_business_profile_cache()
    prep = prepare_chatbot_settle_query("请帮我查询F2(望京)站点的本月的沉降量")
    assert prep.ops_routed is False
    assert prep.forced_tables == ["t_data_wash_fcb"]
    assert prep.library_source in ("parsed", "default")
    assert prep.confirmed_scope is not None
    assert prep.confirmed_scope.get("station_id") == "F2(望京)"
    assert prep.period_eligible is True


@pytest.mark.asyncio
async def test_run_chatbot_period_hit_skips_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    monkeypatch.setenv("CHATBOT_EXPOSE_NL2SQL_SQL_IN_META", "true")
    from app.llm.graphs.chatbot_business_profile import clear_chatbot_business_profile_cache
    from app.llm.graphs.chatbot_nl2sql_answer import run_chatbot_nl2sql_query

    clear_chatbot_business_profile_cache()
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(side_effect=AssertionError("LLM path should not run on period hit"))

    with patch(
        "app.nl2sql.chatbot_settle_prepare.try_chatbot_period_list",
        new=AsyncMock(return_value=("SELECT 1 AS x", [{"x": 1}])),
    ):
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
                            "nl2sql_analysis_max_tokens": 1024,
                            "nl2sql_analysis_timeout_sec": 120.0,
                            "nl2sql_analysis_temperature": 0.2,
                            "nl2sql_analysis_meta_enabled": True,
                        },
                    )()
                },
            )()
            outcome = await run_chatbot_nl2sql_query(
                nl2sql,
                None,
                user_id="u1",
                session_id="s1",
                question="请帮我查询F2(望京)站点的本月的沉降量",
            )
    assert outcome.period_deterministic == "hit"
    assert outcome.ops_routed is False
    assert nl2sql.query.await_count == 0
    assert outcome.gen_failed is False


@pytest.mark.asyncio
async def test_run_chatbot_period_fail_falls_back_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    from app.llm.graphs.chatbot_business_profile import clear_chatbot_business_profile_cache
    from app.llm.graphs.chatbot_nl2sql_answer import run_chatbot_nl2sql_query
    from app.models.nl2sql import NL2SQLQueryResponse

    clear_chatbot_business_profile_cache()
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(
        return_value=NL2SQLQueryResponse(sql="SELECT 2 AS y", rows=[{"y": 2}])
    )

    with patch(
        "app.nl2sql.chatbot_settle_prepare.try_chatbot_period_list",
        new=AsyncMock(return_value=None),
    ):
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
                            "nl2sql_analysis_max_tokens": 1024,
                            "nl2sql_analysis_timeout_sec": 120.0,
                            "nl2sql_analysis_temperature": 0.2,
                            "nl2sql_analysis_meta_enabled": True,
                        },
                    )()
                },
            )()
            outcome = await run_chatbot_nl2sql_query(
                nl2sql,
                None,
                user_id="u1",
                session_id="s1",
                question="请帮我查询F2(望京)站点的本月的沉降量",
            )
    assert outcome.period_deterministic == "fail_fallback_llm"
    assert nl2sql.query.await_count == 1
    req = nl2sql.query.await_args.args[0]
    assert req.forced_tables == ["t_data_wash_fcb"]
    assert req.confirmed_scope is not None


@pytest.mark.asyncio
async def test_run_chatbot_ops_no_settlement_forced_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    from app.llm.graphs.chatbot_business_profile import clear_chatbot_business_profile_cache
    from app.llm.graphs.chatbot_nl2sql_answer import run_chatbot_nl2sql_query
    from app.models.nl2sql import NL2SQLQueryResponse

    clear_chatbot_business_profile_cache()
    nl2sql = MagicMock()
    nl2sql.query = AsyncMock(
        return_value=NL2SQLQueryResponse(sql="SELECT 1", rows=[{"a": 1}])
    )
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
                        "nl2sql_analysis_max_tokens": 1024,
                        "nl2sql_analysis_timeout_sec": 120.0,
                        "nl2sql_analysis_temperature": 0.2,
                        "nl2sql_analysis_meta_enabled": True,
                    },
                )()
            },
        )()
        outcome = await run_chatbot_nl2sql_query(
            nl2sql,
            None,
            user_id="u1",
            session_id="s1",
            question="当前设备在线率是多少",
        )
    assert outcome.ops_routed is True
    req = nl2sql.query.await_args.args[0]
    assert not req.forced_tables
    assert "t_data_wash_fcb" not in (req.forced_tables or [])
