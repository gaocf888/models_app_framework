from __future__ import annotations

from typing import Any

from app.nl2sql.intent_config import (
    inject_parsed_intent_enabled,
    response_include_parsed_intent,
    trace_include_question_intent,
)
from app.nl2sql.question_scope_models import QuestionIntent
from app.nl2sql.time_intent_display import resolve_statistical_time_range_display

__all__ = [
    "format_parsed_intent_prompt_block",
    "inject_parsed_intent_enabled",
    "question_intent_to_dict",
    "response_include_parsed_intent",
    "trace_include_question_intent",
]


def question_intent_to_dict(intent: QuestionIntent) -> dict[str, Any]:
    """结构化问句意图 JSON 可序列化 dict（trace / API / 日志）。"""
    scope = intent.scope
    time_window: dict[str, str] | None = None
    if intent.time_window is not None:
        start, end, tag = intent.time_window
        time_window = {"start_expr": start, "end_expr": end, "tag": tag}
    stat_range = resolve_statistical_time_range_display(intent.scope_question)
    return {
        "parse_mode": intent.parse_mode,
        "scope_question": intent.scope_question,
        "time_window_tag": intent.time_window_tag,
        "time_window": time_window,
        "statistical_time_range": (
            {"start": stat_range[0], "end": stat_range[1]} if stat_range else None
        ),
        "scope": {
            "boiler": scope.boiler,
            "device_name": scope.device_name,
            "piperow_name": scope.piperow_name,
            "row_no": scope.row_no,
            "tube_no": scope.tube_no,
        },
    }


def format_parsed_intent_prompt_block(intent: QuestionIntent) -> str:
    """供 NL2SQL SQL 生成 Prompt 追加的「已识别问句意图」块。"""
    lines = ["【已识别问句意图】"]

    tag = intent.time_window_tag
    stat = resolve_statistical_time_range_display(intent.scope_question)
    if tag and stat:
        lines.append(f"- 时间窗：{tag}（{stat[0]} ~ {stat[1]}）")
    elif tag:
        lines.append(f"- 时间窗：{tag}")
    else:
        lines.append("- 时间窗：未识别")

    scope = intent.scope
    lines.append(f"- 锅炉：{scope.boiler or '全厂/未指定'}")
    if scope.device_name:
        lines.append(f"- 受热面：{scope.device_name}")
    if scope.piperow_name:
        lines.append(f"- 管排：{scope.piperow_name}")
    if scope.row_no is not None:
        lines.append(f"- 排数：{scope.row_no}")
    if scope.tube_no is not None:
        lines.append(f"- 管数：{scope.tube_no}")

    return "\n".join(lines)
