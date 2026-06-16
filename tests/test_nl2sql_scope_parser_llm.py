"""Phase 3：LLM 范围解析与 fallback。"""

from __future__ import annotations

import json

import pytest

from app.core.config import get_app_config
from app.llm.prompt_registry import PromptTemplate, PromptTemplateRegistry
from app.nl2sql.question_intent import resolve_question_intent
from app.nl2sql.scope_parser_llm import (
    ScopeParseLLMError,
    build_scope_parse_prompt,
    finalize_llm_scope,
    parse_llm_scope_output,
    parse_scope_llm_sync,
    resolve_scope_with_mode,
)
from app.nl2sql.scope_parser_rule import parse_scope_rule


class _FakeLLMClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.last_messages: list[dict[str, str]] | None = None

    async def chat(self, model: str, messages: list[dict[str, str]], **kwargs: object) -> str:
        self.last_messages = messages
        return self.response


def _registry_with_scope_prompt() -> PromptTemplateRegistry:
    reg = PromptTemplateRegistry.__new__(PromptTemplateRegistry)
    reg._config_path = "test"
    reg._templates = {
        "nl2sql_scope_parse": [
            PromptTemplate(
                scene="nl2sql_scope_parse",
                version="v1",
                weight=1.0,
                description="test",
                content="Parse scope for: {{QUESTION}}",
            )
        ]
    }
    return reg


@pytest.fixture(autouse=True)
def _clear_nl2sql_intent_config_cache() -> None:
    get_app_config.cache_clear()
    yield  # type: ignore[misc]
    get_app_config.cache_clear()


def test_parse_llm_scope_output_json_fence() -> None:
    raw = '```json\n{"device_name":"低温过热器","row_no":1,"tube_no":1}\n```'
    parsed = parse_llm_scope_output(raw)
    assert parsed.device_name == "低温过热器"
    assert parsed.row_no == 1
    assert parsed.tube_no == 1


def test_parse_llm_scope_output_invalid_raises() -> None:
    with pytest.raises(ScopeParseLLMError):
        parse_llm_scope_output("not json")


def test_finalize_llm_scope_boiler_always_from_rule() -> None:
    rule = parse_scope_rule("1号锅炉低温过热器第一层第一排第一根")
    parsed = parse_llm_scope_output(
        json.dumps(
            {
                "boiler": "2号锅炉",
                "device_name": "低过",
                "piperow_name": "前屏",
                "row_no": 1,
                "tube_no": 1,
            }
        )
    )
    scope = finalize_llm_scope(
        parsed,
        scope_question="1号锅炉低温过热器第一层第一排第一根",
        rule_scope=rule,
    )
    assert scope.boiler == "1号锅炉"
    assert scope.device_name == "低温过热器"
    assert scope.piperow_name == "第一屏"


def test_build_scope_parse_prompt_substitutes_question() -> None:
    prompt = build_scope_parse_prompt(
        "1号锅炉超温",
        prompt_registry=_registry_with_scope_prompt(),
    )
    assert "1号锅炉超温" in prompt
    assert "{{QUESTION}}" not in prompt


def test_resolve_scope_with_mode_rule() -> None:
    scope, mode = resolve_scope_with_mode(
        "1号锅炉低温过热器第一层第一排第一根",
        mode="rule",
    )
    assert mode == "rule"
    assert scope.device_name == "低温过热器"
    assert scope.row_no == 1


def test_resolve_scope_with_mode_llm_success() -> None:
    llm = _FakeLLMClient(
        json.dumps(
            {
                "boiler": "9号锅炉",
                "device_name": "屏式过热器",
                "piperow_name": "前屏",
                "row_no": 2,
                "tube_no": 3,
            }
        )
    )
    q = "2号机组屏式过热器前屏第二排第三根"
    scope, mode = resolve_scope_with_mode(q, mode="llm", llm_client=llm)
    assert mode == "llm"
    assert scope.boiler == "2号锅炉"
    assert scope.device_name == "屏式过热器"
    assert scope.piperow_name == "第一屏"
    assert scope.row_no == 2
    assert scope.tube_no == 3


def test_resolve_scope_with_mode_llm_fallback_on_bad_json() -> None:
    llm = _FakeLLMClient("抱歉，无法解析")
    q = "1号锅炉低温过热器第一层第一排第一根"
    scope, mode = resolve_scope_with_mode(q, mode="rule_with_llm_fallback", llm_client=llm)
    assert mode == "llm_fallback_rule"
    assert scope.device_name == "低温过热器"
    assert scope.row_no == 1


def test_parse_scope_llm_sync_water_wall_default_row() -> None:
    llm = _FakeLLMClient(
        json.dumps(
            {
                "device_name": "水冷壁左墙",
                "piperow_name": "炉后向炉前数",
                "row_no": None,
                "tube_no": 1,
            }
        )
    )
    scope = parse_scope_llm_sync(
        "二号机组水冷壁左墙炉后向炉前数第一根",
        llm_client=llm,
    )
    assert scope.boiler == "2号锅炉"
    assert scope.row_no == 1
    assert scope.tube_no == 1


def test_resolve_question_intent_llm_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.nl2sql.question_scope_models import QuestionScopeIntent

    monkeypatch.setenv("NL2SQL_INTENT_PARSE_MODE", "llm")
    monkeypatch.setattr(
        "app.nl2sql.scope_parser_llm.parse_scope_llm_sync",
        lambda scope_question, **kw: QuestionScopeIntent(
            boiler="1号锅炉",
            device_name="低温过热器",
            piperow_name="第一层",
            row_no=1,
            tube_no=1,
        ),
    )
    intent = resolve_question_intent("1号锅炉低温过热器第一层第一排第一根")
    assert intent.parse_mode == "llm"
    assert intent.scope.device_name == "低温过热器"
    assert intent.time_window_tag is None


def test_resolve_scope_with_mode_logs_rule_llm_diff(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    monkeypatch.setenv("NL2SQL_SCOPE_PARSE_LOG_RULE_LLM_DIFF", "true")
    get_app_config.cache_clear()
    llm = _FakeLLMClient(
        json.dumps(
            {
                "device_name": "屏式过热器",
                "piperow_name": "前屏",
                "row_no": 2,
                "tube_no": 3,
            }
        )
    )
    with caplog.at_level(logging.INFO):
        resolve_scope_with_mode(
            "1号锅炉低温过热器第一层第一排第一根",
            mode="llm",
            llm_client=llm,
        )
    assert any("NL2SQL scope rule vs LLM diff" in rec.message for rec in caplog.records)
    get_app_config.cache_clear()
