"""NL2SQL 问句意图相关配置访问（统一读取 ``AppConfig.nl2sql_intent``）。"""

from __future__ import annotations

from app.core.config import get_app_config


def nl2sql_intent_config():
    return get_app_config().nl2sql_intent


def scope_sql_rewrite_enabled() -> bool:
    return nl2sql_intent_config().scope_sql_rewrite_enabled


def scope_lexicon_file() -> str | None:
    return nl2sql_intent_config().scope_lexicon_file


def intent_parse_mode() -> str:
    return nl2sql_intent_config().intent_parse_mode


def scope_parse_llm_timeout_seconds() -> float:
    return nl2sql_intent_config().scope_parse_llm_timeout_ms / 1000.0


def scope_parse_prompt_version() -> str:
    return nl2sql_intent_config().scope_parse_prompt_version


def scope_parse_llm_max_tokens() -> int:
    return nl2sql_intent_config().scope_parse_llm_max_tokens


def scope_parse_llm_temperature() -> float:
    return nl2sql_intent_config().scope_parse_llm_temperature


def scope_parse_log_rule_llm_diff() -> bool:
    return nl2sql_intent_config().scope_parse_log_rule_llm_diff


def inject_parsed_intent_enabled() -> bool:
    return nl2sql_intent_config().inject_parsed_intent


def response_include_parsed_intent() -> bool:
    return nl2sql_intent_config().response_include_parsed_intent


def trace_include_question_intent() -> bool:
    return nl2sql_intent_config().trace_include_question_intent
