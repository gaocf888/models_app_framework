"""Phase 4：问句意图可观测性与 Prompt 注入。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import get_app_config
from app.models.nl2sql import NL2SQLQueryRequest
from app.nl2sql.chain import NL2SQLChain, NL2SQLValidationContext
from app.nl2sql.errors import NL2SQLExecutionError
from app.nl2sql.question_intent import (
    resolve_question_intent,
    scope_literals_from_intent,
    scope_literals_from_parsed_intent,
)
from app.nl2sql.question_intent_display import (
    format_parsed_intent_prompt_block,
    inject_parsed_intent_enabled,
    question_intent_to_dict,
    response_include_parsed_intent,
)
from app.services.nl2sql_service import NL2SQLService


def test_question_intent_to_dict_structure() -> None:
    intent = resolve_question_intent("1号锅炉低温过热器第一层第一排第一根")
    data = question_intent_to_dict(intent)
    assert data["parse_mode"] == "rule"
    assert data["scope"]["boiler"] == "1号锅炉"
    assert data["scope"]["device_name"] == "低温过热器"
    assert data["scope"]["row_no"] == 1


def test_format_parsed_intent_prompt_block() -> None:
    intent = resolve_question_intent("请分析1号锅炉前天的超温")
    block = format_parsed_intent_prompt_block(intent)
    assert "【已识别问句意图】" in block
    assert "1号锅炉" in block
    assert "时间窗" in block


def test_phase4_env_defaults_off() -> None:
    get_app_config.cache_clear()
    assert not inject_parsed_intent_enabled()
    assert not response_include_parsed_intent()
    get_app_config.cache_clear()


@pytest.mark.asyncio
async def test_nl2sql_service_include_parsed_intent_flag() -> None:
    chain = MagicMock()
    chain.generate_sql_with_validation_context = AsyncMock(
        return_value=(
            "SELECT 1",
            NL2SQLValidationContext(
                allowed_tables=frozenset(),
                allowed_columns=frozenset(),
                schema_ok=False,
                table_columns={},
                join_whitelist=frozenset(),
                parsed_intent={"parse_mode": "rule", "scope": {"boiler": "1号锅炉"}},
            ),
        )
    )
    executor = MagicMock()
    executor.execute = AsyncMock(return_value=[{"x": 1}])
    service = NL2SQLService(chain=chain, executor=executor)

    req = NL2SQLQueryRequest(
        user_id="u1",
        session_id="s1",
        question="1号锅炉超温",
    )
    resp_hidden = await service.query(req, record_conversation=False, include_parsed_intent=False)
    assert resp_hidden.parsed_intent is None

    resp_show = await service.query(req, record_conversation=False, include_parsed_intent=True)
    assert resp_show.parsed_intent is not None
    assert resp_show.parsed_intent["scope"]["boiler"] == "1号锅炉"


def test_prompt_block_only_lists_populated_scope_fields() -> None:
    intent = resolve_question_intent("请分析近一周超温")
    block = format_parsed_intent_prompt_block(intent)
    assert "受热面" not in block
    assert "近一周" in block or "recent_7_days" in block or "时间窗" in block


def test_scope_literals_from_parsed_intent_roundtrip() -> None:
    intent = resolve_question_intent("1号锅炉低温过热器第一层第一排第一根")
    parsed = question_intent_to_dict(intent)
    literals = scope_literals_from_parsed_intent(parsed)
    assert literals == scope_literals_from_intent(intent)


def test_rewrite_entity_scope_literals_reuses_precomputed_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _fake_resolve(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return resolve_question_intent(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("app.nl2sql.question_intent.resolve_question_intent", _fake_resolve)
    chain = NL2SQLChain.__new__(NL2SQLChain)
    intent = resolve_question_intent("1号锅炉低温过热器")
    precomputed = scope_literals_from_intent(intent)
    calls["n"] = 0

    sql = "SELECT 1 WHERE @unit_keyword = @unit_keyword"
    chain._rewrite_entity_scope_literals(
        sql,
        question="1号锅炉低温过热器",
        scope_literals=precomputed,
    )
    assert calls["n"] == 0

    chain._rewrite_entity_scope_literals(sql, question="1号锅炉低温过热器")
    assert calls["n"] == 1


def test_rewrite_query_filters_reuses_precomputed_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def _fake_extract(*args: object, **kwargs: object) -> object:
        calls["n"] += 1
        return NL2SQLChain._extract_scope_literals_from_question(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(NL2SQLChain, "_extract_scope_literals_from_question", _fake_extract)
    chain = NL2SQLChain.__new__(NL2SQLChain)
    intent = resolve_question_intent("1号锅炉")
    precomputed = scope_literals_from_intent(intent)
    calls["n"] = 0

    chain._rewrite_query_filters(
        "SELECT 1 WHERE @unit_keyword = @unit_keyword",
        question="1号锅炉",
        scope_literals=precomputed,
    )
    assert calls["n"] == 0
