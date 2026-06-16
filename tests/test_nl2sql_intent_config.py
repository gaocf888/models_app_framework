"""NL2SQLIntentConfig 环境变量加载。"""

from __future__ import annotations

import os

from app.core.config import _load_from_env, get_app_config


def test_nl2sql_intent_config_defaults() -> None:
    get_app_config.cache_clear()
    cfg = _load_from_env().nl2sql_intent
    assert cfg.intent_parse_mode == "rule"
    assert cfg.scope_sql_rewrite_enabled is False
    assert cfg.inject_parsed_intent is False
    assert cfg.response_include_parsed_intent is False
    assert cfg.trace_include_question_intent is True
    assert cfg.scope_parse_log_rule_llm_diff is False
    get_app_config.cache_clear()


def test_nl2sql_intent_config_from_env(monkeypatch) -> None:
    get_app_config.cache_clear()
    monkeypatch.setenv("NL2SQL_INTENT_PARSE_MODE", "llm")
    monkeypatch.setenv("NL2SQL_SCOPE_SQL_REWRITE_ENABLED", "true")
    monkeypatch.setenv("NL2SQL_INJECT_PARSED_INTENT", "true")
    monkeypatch.setenv("NL2SQL_SCOPE_PARSE_LOG_RULE_LLM_DIFF", "true")
    cfg = _load_from_env().nl2sql_intent
    assert cfg.intent_parse_mode == "llm"
    assert cfg.scope_sql_rewrite_enabled is True
    assert cfg.inject_parsed_intent is True
    assert cfg.scope_parse_log_rule_llm_diff is True
    get_app_config.cache_clear()
