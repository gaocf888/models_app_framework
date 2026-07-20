"""智能客服人机协同（HITL）：意图路由确认与 NL2SQL 生成失败降级。"""

from __future__ import annotations

import time
from typing import Any

from app.core.config import get_app_config
from app.llm.graphs.chatbot_hitl_display import (
    ACTION_FALLBACK_KB_QA,
    ACTION_NL2SQL_RETRY,
    ACTION_ROUTE_CLARIFY,
    ACTION_ROUTE_DATA_QUERY,
    ACTION_ROUTE_KB_QA,
    HITL_KIND_INTENT_ROUTE,
    HITL_KIND_NL2SQL_GEN_FAILED,
    build_hitl_interrupt_payload,
    hitl_button_label,
)
from app.llm.graphs.chatbot_intent_rules import _has_conceptual, _has_data

_DATA_HINT_WORDS = ("多少", "列表", "查询", "当前", "台账", "统计", "记录", "查一下", "查下")
_CONCEPT_HINT_WORDS = ("为什么", "原因", "机理", "原理", "如何形成", "如何预防", "标准", "规定")


class ChatbotHitlValidationError(ValueError):
    """HITL 续跑参数校验失败（如 route_clarify 缺少 refined_query）。"""


def _cfg():
    return get_app_config().chatbot


def hitl_globally_enabled() -> bool:
    return bool(_cfg().hitl_enabled)


def intent_hitl_enabled() -> bool:
    return hitl_globally_enabled() and bool(_cfg().intent_hitl_enabled)


def nl2sql_hitl_enabled() -> bool:
    return hitl_globally_enabled() and bool(_cfg().nl2sql_hitl_enabled)


def should_trigger_intent_hitl(state: dict[str, Any]) -> bool:
    """窄触发：边界意图才弹确认；已有 confirmed_route 或规则 clarify 不触发。"""
    if not intent_hitl_enabled():
        return False
    if state.get("confirmed_route"):
        return False
    label = str(state.get("intent_label") or "kb_qa").lower()
    if label == "clarify":
        return False
    if state.get("image_urls"):
        return False
    prev = str(state.get("intent_prev_task_type") or "")
    if prev == "after_intent_confirm":
        return False
    if prev == "data_query_thread" and label == "data_query":
        conf = float(state.get("intent_confidence") or 0.0)
        if conf >= 0.75:
            return False

    q = str(state.get("query") or "").strip()
    reason = str(state.get("intent_reason") or "")
    conf = float(state.get("intent_confidence") or 0.0)
    min_conf = float(_cfg().intent_hitl_min_confidence)

    if conf >= 0.78 and reason == "structured_query_heuristic":
        return False
    if conf >= 0.80 and reason == "conceptual_qa_heuristic":
        return False
    if conf >= 0.78 and reason == "default_kb_qa":
        return False

    if conf < min_conf:
        return True
    if "mixed_" in reason:
        return True
    if "ambiguous_" in reason:
        return True
    if label == "data_query" and _has_conceptual(q):
        return True
    if label == "kb_qa" and _has_data(q):
        return True
    if any(w in q for w in _CONCEPT_HINT_WORDS) and any(w in q for w in _DATA_HINT_WORDS):
        return True
    return False


def prepare_intent_hitl_patch(state: dict[str, Any]) -> dict[str, Any]:
    original = str(state.get("query") or "").strip()
    return {
        "pending_hitl": True,
        "hitl_kind": HITL_KIND_INTENT_ROUTE,
        "hitl_original_query": original,
        "status": "awaiting_hitl",
        "terminate_reason": "hitl_intent_confirm",
        "answer_text": "",
        "llm_messages": [],
    }


def should_trigger_nl2sql_hitl(state: dict[str, Any], *, gen_failed: bool) -> bool:
    if not gen_failed or not nl2sql_hitl_enabled():
        return False
    retry_count = int(state.get("nl2sql_retry_count") or 0)
    return retry_count < max(0, int(_cfg().nl2sql_hitl_max_retries))


def prepare_nl2sql_hitl_patch(state: dict[str, Any], *, fail_reason: str | None) -> dict[str, Any]:
    original = str(state.get("hitl_original_query") or state.get("query") or "").strip()
    return {
        "pending_hitl": True,
        "hitl_kind": HITL_KIND_NL2SQL_GEN_FAILED,
        "hitl_original_query": original,
        "nl2sql_fail_reason": (fail_reason or "empty_sql").strip(),
        "nl2sql_failed": True,
        "status": "awaiting_hitl",
        "terminate_reason": "hitl_nl2sql_failed",
        "answer_text": "",
        "llm_messages": [],
    }


def build_nl2sql_retry_hint(fail_reason: str | None) -> str:
    reason = (fail_reason or "未能生成有效 SQL").strip()
    return (
        f"【上轮失败】校验未通过：{reason}\n"
        "【要求】请修正 SQL：避免引用子查询派生列 alias 作为物理列；"
        "优先使用相关子查询或仅使用 schema 白名单内的物理列名；"
        "若问句含「当前/实时」，可用 ORDER BY 时间 DESC LIMIT 1 替代复杂 JOIN 最新行。"
    )


def apply_chatbot_hitl_action(
    state: dict[str, Any],
    *,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """解析用户按钮选择，写入 confirmed_route 与 NL2SQL 重试参数。"""
    payload = dict(payload or {})
    out = dict(state)
    interactions = list(out.get("human_interactions") or [])
    interactions.append(
        {
            "action": action,
            "payload": payload,
            "ts": time.time(),
        }
    )
    out["human_interactions"] = interactions
    out["pending_hitl"] = False
    out["hitl_resume_action"] = action
    out["status"] = "intented"

    refined = str(payload.get("refined_query") or "").strip()

    if action == ACTION_ROUTE_DATA_QUERY:
        out["confirmed_route"] = "data_query"
        out["intent_label"] = "data_query"
        if refined:
            out["query"] = refined
    elif action == ACTION_ROUTE_KB_QA:
        out["confirmed_route"] = "kb_qa"
        out["intent_label"] = "kb_qa"
        if refined:
            out["query"] = refined
    elif action == ACTION_ROUTE_CLARIFY:
        if not refined:
            raise ChatbotHitlValidationError("refined_query is required for route_clarify")
        out["query"] = refined
        out["confirmed_route"] = ""
        out["intent_prev_task_type"] = "after_intent_confirm"
    elif action == ACTION_NL2SQL_RETRY:
        out["confirmed_route"] = "data_query"
        out["intent_label"] = "data_query"
        out["nl2sql_retry_count"] = int(out.get("nl2sql_retry_count") or 0) + 1
        out["nl2sql_skip_cache"] = True
        prev_reason = str(out.get("nl2sql_fail_reason") or "")
        out["nl2sql_retry_hint"] = build_nl2sql_retry_hint(prev_reason)
        out["nl2sql_failed"] = False
        out["nl2sql_fail_reason"] = None
        if refined:
            out["query"] = refined
    elif action == ACTION_FALLBACK_KB_QA:
        out["confirmed_route"] = "kb_qa"
        out["intent_label"] = "kb_qa"
        out["used_nl2sql"] = False
        out["nl2sql_failed"] = False
        base_q = str(out.get("hitl_original_query") or out.get("query") or "")
        out["query"] = refined or base_q
    else:
        raise ValueError(f"unsupported chatbot hitl action: {action}")

    out["terminate_reason"] = None
    return out


def build_hitl_sse_event(state: dict[str, Any], *, resume_token: str) -> dict[str, Any]:
    payload = build_hitl_interrupt_payload(state)
    return {
        "type": "chatbot_hitl_required",
        "hitl_id": f"hitl_{int(time.time() * 1000)}",
        "resume_token": resume_token,
        **payload,
    }
