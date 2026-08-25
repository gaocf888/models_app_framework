from __future__ import annotations

from typing import Any, Literal

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.intent_config import intent_parse_mode
from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
from app.nl2sql.scope_parser_llm import resolve_scope_with_mode
from app.nl2sql.time_intent_display import (
    extract_time_anchor_from_question,
    extract_time_window_from_question,
)

ParseMode = Literal["rule", "llm", "llm_fallback_rule", "human_confirmed"]

ScopeLiterals = dict[str, str | int | None]


def _adapt_time_window_tuple(
    win: tuple[str, str, str] | None,
) -> tuple[str, str, str] | None:
    if win is None:
        return None
    from app.nl2sql.sql_dialect import adapt_time_window

    start, end, tag = win
    start, end = adapt_time_window(start, end)
    return start, end, tag


def _scope_from_confirmed_dict(confirmed: dict[str, Any]) -> QuestionScopeIntent:
    data = dict(confirmed or {})
    if not data.get("check_location_name") and data.get("piperow_name"):
        data["check_location_name"] = data.get("piperow_name")
    row = data.get("row_no")
    tube = data.get("tube_no")
    return QuestionScopeIntent(
        boiler=data.get("boiler") or None,
        device_name=data.get("device_name") or None,
        check_location_name=data.get("check_location_name") or None,
        row_no=int(row) if isinstance(row, int) and row > 0 else None,
        tube_no=int(tube) if isinstance(tube, int) and tube > 0 else None,
        station_id=str(data.get("station_id") or "") or None,
        station_name=str(data.get("station_name") or "") or None,
        district=str(data.get("district") or data.get("area") or "") or None,
        device_type=str(data.get("device_type") or "") or None,
    )


def resolve_question_intent(
    question: str,
    *,
    time_intent_source: str | None = None,
    parse_mode: str | None = None,
    confirmed_scope: dict[str, Any] | None = None,
    scope_intent_text: str | None = None,
    original_query: str | None = None,
) -> QuestionIntent:
    """
    统一问句意图解析入口。

    - 时间：始终走 ``time_intent_display`` 程序规则；
    - 范围：``NL2SQL_INTENT_PARSE_MODE`` 为 ``rule``（默认）/ ``llm`` / ``rule_with_llm_fallback``；
    - 看图诊断 HITL：传入 ``confirmed_scope`` 时 scope 以人工确认为准（``human_confirmed``）。
    """
    raw = (question or "").strip()
    mode_raw = (parse_mode or intent_parse_mode()).strip().lower()

    if confirmed_scope:
        scope_q = (scope_intent_text or time_intent_source or raw).strip()
        scope = _scope_from_confirmed_dict(confirmed_scope)
        time_window = _adapt_time_window_tuple(extract_time_window_from_question(scope_q))
        time_anchor = extract_time_anchor_from_question(scope_q)
        if not time_window and original_query:
            time_window = _adapt_time_window_tuple(
                extract_time_window_from_question(original_query.strip())
            )
        if not time_anchor and original_query:
            time_anchor = extract_time_anchor_from_question(original_query.strip())
        return QuestionIntent(
            raw_question=raw,
            scope_question=scope_q,
            time_window=time_window,
            scope=scope,
            time_anchor=time_anchor,
            parse_mode="human_confirmed",
        )

    scope_q = NL2SQLChain._resolve_entity_scope_question(
        question=raw,
        time_intent_source=time_intent_source,
    )
    scope, effective_mode = resolve_scope_with_mode(scope_q, mode=mode_raw)
    time_window = _adapt_time_window_tuple(extract_time_window_from_question(scope_q))
    time_anchor = extract_time_anchor_from_question(scope_q)

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
        time_anchor=time_anchor,
        parse_mode=parse_mode_out,
    )


def scope_literals_from_intent(intent: QuestionIntent) -> ScopeLiterals:
    """从已解析 QuestionIntent 提取 scope 字面量（避免 SQL 改写路径重复解析）。"""
    s = intent.scope
    unit_kw = s.boiler
    out: ScopeLiterals = {
        "unit_keyword": unit_kw,
        "boiler": unit_kw,
        "device_name": s.device_name,
        "check_location_name": s.check_location_name or s.piperow_name,
        "piperow_name": s.piperow_name,
        "row_no": s.row_no,
        "tube_no": s.tube_no,
        "station_id": s.station_id,
        "station_name": s.station_name,
        "district": s.district,
        "area": s.district,
        "device_type": s.device_type,
    }
    return out


def scope_literals_from_parsed_intent(
    parsed_intent: dict[str, Any] | None,
) -> ScopeLiterals | None:
    """从 validation_ctx.parsed_intent 恢复 scope 字面量。"""
    if not parsed_intent:
        return None
    scope = parsed_intent.get("scope") or {}
    scope = dict(scope)
    if not scope.get("check_location_name") and scope.get("piperow_name"):
        scope["check_location_name"] = scope.get("piperow_name")
    boiler = scope.get("boiler")
    return {
        "unit_keyword": boiler,
        "boiler": boiler,
        "device_name": scope.get("device_name"),
        "check_location_name": scope.get("check_location_name"),
        "piperow_name": scope.get("piperow_name"),
        "row_no": scope.get("row_no"),
        "tube_no": scope.get("tube_no"),
        "station_id": scope.get("station_id"),
        "station_name": scope.get("station_name"),
        "district": scope.get("district") or scope.get("area"),
        "area": scope.get("district") or scope.get("area"),
        "device_type": scope.get("device_type"),
    }


def scope_literals_from_question(
    question: str,
    *,
    time_intent_source: str | None = None,
) -> ScopeLiterals:
    """供 NL2SQLChain 使用的 scope 字面量 dict（向后兼容 unit_keyword/boiler）。"""
    intent = resolve_question_intent(question, time_intent_source=time_intent_source)
    return scope_literals_from_intent(intent)
