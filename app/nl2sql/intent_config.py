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


def anchor_fallback_now_enabled() -> bool:
    return nl2sql_intent_config().anchor_fallback_now_enabled


def anchor_fallback_analysis_types() -> frozenset[str]:
    raw = nl2sql_intent_config().anchor_fallback_analysis_types or ""
    parts = {p.strip() for p in raw.split(",") if p.strip()}
    return frozenset(parts)


def reject_unresolved_time_placeholders() -> bool:
    return nl2sql_intent_config().reject_unresolved_time_placeholders


def semantic_link_enabled() -> bool:
    return nl2sql_intent_config().semantic_link_enabled


def schema_link_catalog_mode() -> str:
    return nl2sql_intent_config().schema_link_catalog_mode


def on_link_failure_default() -> str:
    return nl2sql_intent_config().on_link_failure


def sql_dialect() -> str:
    return nl2sql_intent_config().sql_dialect


def business_domain() -> str | None:
    return nl2sql_intent_config().business_domain


def semantic_dict_path() -> str | None:
    return nl2sql_intent_config().semantic_dict_path


def entity_rules_file() -> str | None:
    return nl2sql_intent_config().entity_rules_file


def table_allowlist_fingerprint() -> str:
    return nl2sql_intent_config().table_allowlist_fingerprint or ""
