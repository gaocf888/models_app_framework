"""智能客服 HITL：话术与按钮定义。"""

from __future__ import annotations

from typing import Any

HITL_KIND_INTENT_ROUTE = "intent_route_confirm"
HITL_KIND_INTENT_DISAMBIGUATION = "intent_disambiguation_suggest"
HITL_KIND_NL2SQL_GEN_FAILED = "nl2sql_gen_failed"

ACTION_ROUTE_DATA_QUERY = "route_data_query"
ACTION_ROUTE_KB_QA = "route_kb_qa"
ACTION_ROUTE_CLARIFY = "route_clarify"
ACTION_NL2SQL_RETRY = "nl2sql_retry"
ACTION_FALLBACK_KB_QA = "fallback_kb_qa"
ACTION_PICK_DISAMBIGUATION_OPTION = "pick_disambiguation_option"

INTENT_ROUTE_BUTTONS: list[dict[str, str]] = [
    {"id": ACTION_ROUTE_DATA_QUERY, "label": "查实时/台账数据"},
    {"id": ACTION_ROUTE_KB_QA, "label": "基于知识库分析"},
    {"id": ACTION_ROUTE_CLARIFY, "label": "我先补充问题"},
]

NL2SQL_FAIL_BUTTONS: list[dict[str, str]] = [
    {"id": ACTION_NL2SQL_RETRY, "label": "重试查数"},
    {"id": ACTION_FALLBACK_KB_QA, "label": "基于知识库分析"},
]


def disambiguation_button_id(index: int) -> str:
    return f"pick_disambiguation_{int(index)}"


def build_intent_hitl_prompt(*, query: str, intent_label: str) -> str:
    q = (query or "").strip()
    hint = ""
    if intent_label == "data_query":
        hint = "系统倾向于从业务数据库查询结构化数据。"
    elif intent_label == "kb_qa":
        hint = "系统倾向于从知识库检索说明类内容。"
    else:
        hint = "系统需要您确认希望的处理方式。"
    return (
        f"您的问题：「{q}」\n\n"
        f"{hint}\n\n"
        "请选择您希望我采用的方式："
    )


def build_nl2sql_hitl_prompt(*, query: str, fail_reason: str | None) -> str:
    q = (query or "").strip()
    reason = (fail_reason or "未能生成有效 SQL").strip()
    return (
        f"未能完成数据查询：「{q}」\n\n"
        f"原因：{reason}\n\n"
        "您可以重试查数，或改为从知识库检索相关说明。"
    )


def build_disambiguation_hitl_prompt(
    *,
    analysis: str,
    options: list[dict[str, Any]],
) -> str:
    lines = [(analysis or "").strip() or "您的问题意图不够明确，请选择更具体的问法：", ""]
    for i, opt in enumerate(options or []):
        title = str(opt.get("title") or "").strip()
        query = str(opt.get("query") or "").strip()
        route = str(opt.get("route_hint") or "").strip()
        route_zh = "查数" if route == "data_query" else "知识库"
        lines.append(f"{i + 1}. 【{route_zh}】{title}")
        if query and query != title:
            lines.append(f"   {query}")
    lines.append("")
    lines.append("请点击下方按钮选择一项继续：")
    return "\n".join(lines).strip()


def build_disambiguation_ui_buttons(options: list[dict[str, Any]]) -> list[dict[str, str]]:
    buttons: list[dict[str, str]] = []
    for i, opt in enumerate(options or []):
        title = str(opt.get("title") or opt.get("query") or f"选项{i + 1}").strip()
        buttons.append({"id": disambiguation_button_id(i), "label": title[:32]})
    return buttons


def format_hitl_assistant_message(*, hitl_kind: str, prompt: str) -> str:
    return (prompt or "").strip()


def format_hitl_user_choice_message(*, action: str, label: str | None = None) -> str:
    if label:
        return f"[用户选择] {label}"
    mapping = {
        ACTION_ROUTE_DATA_QUERY: "查实时/台账数据",
        ACTION_ROUTE_KB_QA: "基于知识库分析",
        ACTION_ROUTE_CLARIFY: "补充问题",
        ACTION_NL2SQL_RETRY: "重试查数",
        ACTION_FALLBACK_KB_QA: "基于知识库分析",
        ACTION_PICK_DISAMBIGUATION_OPTION: "消歧选项",
    }
    if action in mapping:
        return f"[用户选择] {mapping[action]}"
    if str(action).startswith("pick_disambiguation_"):
        return f"[用户选择] {action}"
    return f"[用户选择] {action}"


def hitl_button_label(action: str) -> str | None:
    for btn in INTENT_ROUTE_BUTTONS + NL2SQL_FAIL_BUTTONS:
        if btn["id"] == action:
            return btn["label"]
    return None


def build_hitl_interrupt_payload(state: dict[str, Any]) -> dict[str, Any]:
    kind = str(state.get("hitl_kind") or "")
    options = list(state.get("disambiguation_options") or [])
    if kind == HITL_KIND_INTENT_ROUTE:
        buttons = INTENT_ROUTE_BUTTONS
        prompt = build_intent_hitl_prompt(
            query=str(state.get("hitl_original_query") or state.get("query") or ""),
            intent_label=str(state.get("intent_label") or ""),
        )
    elif kind == HITL_KIND_INTENT_DISAMBIGUATION:
        buttons = build_disambiguation_ui_buttons(options)
        prompt = build_disambiguation_hitl_prompt(
            analysis=str(state.get("disambiguation_analysis") or ""),
            options=options,
        )
    else:
        buttons = NL2SQL_FAIL_BUTTONS
        prompt = build_nl2sql_hitl_prompt(
            query=str(state.get("hitl_original_query") or state.get("query") or ""),
            fail_reason=str(state.get("nl2sql_fail_reason") or ""),
        )
    return {
        "hitl_kind": kind,
        "prompt": prompt,
        "ui_buttons": buttons,
        "context": {
            "intent_label": state.get("intent_label"),
            "intent_confidence": state.get("intent_confidence"),
            "intent_reason": state.get("intent_reason"),
            "original_query": state.get("hitl_original_query") or state.get("query"),
            "nl2sql_fail_reason": state.get("nl2sql_fail_reason"),
            "intent_hitl_round": state.get("intent_hitl_round"),
            "disambiguation_options": options if kind == HITL_KIND_INTENT_DISAMBIGUATION else None,
        },
    }
