from __future__ import annotations

from typing import Literal

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.intent_config import intent_parse_mode
from app.nl2sql.question_scope_models import QuestionIntent
from app.nl2sql.scope_parser_llm import resolve_scope_with_mode
from app.nl2sql.time_intent_display import extract_time_window_from_question

ParseMode = Literal["rule", "llm", "llm_fallback_rule"]

ScopeLiterals = dict[str, str | int | None]


def resolve_question_intent(
    question: str,
    *,
    time_intent_source: str | None = None,
    parse_mode: str | None = None,
) -> QuestionIntent:
    """
    统一问句意图解析入口。

    - 时间：始终走 ``time_intent_display`` 程序规则；
    - 范围：``NL2SQL_INTENT_PARSE_MODE`` 为 ``rule``（默认）/ ``llm`` / ``rule_with_llm_fallback``。
    """
    raw = (question or "").strip()
    mode_raw = (parse_mode or intent_parse_mode()).strip().lower()

    scope_q = NL2SQLChain._resolve_entity_scope_question(
        question=raw,
        time_intent_source=time_intent_source,
    )
    scope, effective_mode = resolve_scope_with_mode(scope_q, mode=mode_raw)
    time_window = extract_time_window_from_question(scope_q)

    parse_mode_out: ParseMode = "rule"
    if effective_mode == "llm":
        parse_mode_out = "llm"
    elif effective_mode == "llm_fallback_rule":
        parse_mode_out = "llm_fallback_rule"

    return QuestionIntent(
        raw_question=raw,
        scope_question=scope_q,
        time_window=time_window,
        scope=scope,
        parse_mode=parse_mode_out,
    )


def scope_literals_from_intent(intent: QuestionIntent) -> ScopeLiterals:
    """从已解析 QuestionIntent 提取 scope 字面量（避免 SQL 改写路径重复解析）。"""
    s = intent.scope
    unit_kw = s.boiler
    return {
        "unit_keyword": unit_kw,
        "boiler": unit_kw,
        "device_name": s.device_name,
        "piperow_name": s.piperow_name,
        "row_no": s.row_no,
        "tube_no": s.tube_no,
    }


def scope_literals_from_parsed_intent(
    parsed_intent: dict[str, Any] | None,
) -> ScopeLiterals | None:
    """从 validation_ctx.parsed_intent 恢复 scope 字面量。"""
    if not parsed_intent:
        return None
    scope = parsed_intent.get("scope") or {}
    boiler = scope.get("boiler")
    return {
        "unit_keyword": boiler,
        "boiler": boiler,
        "device_name": scope.get("device_name"),
        "piperow_name": scope.get("piperow_name"),
        "row_no": scope.get("row_no"),
        "tube_no": scope.get("tube_no"),
    }


def scope_literals_from_question(
    question: str,
    *,
    time_intent_source: str | None = None,
) -> ScopeLiterals:
    """供 NL2SQLChain 使用的 scope 字面量 dict（向后兼容 unit_keyword/boiler）。"""
    intent = resolve_question_intent(question, time_intent_source=time_intent_source)
    return scope_literals_from_intent(intent)
