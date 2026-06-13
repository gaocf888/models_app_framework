"""
智能客服：模式 B 意图 LLM（规则主判 + 进程内轻量模型窄触发）。

流程：硬规则闸 → rules 主启发式 → 边界场景 → 本地 Qwen2.5-0.5B-Instruct（CPU）
→ JSON 校验 → 失败回退 rules。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.chatbot_intent_llm_local import ChatbotIntentLocalLlm

from .chatbot_intent_rules import (
    IntentRuleResult,
    apply_intent_hard_gates,
    build_intent_context_from_history,
    classify_chatbot_intent_by_rules,
)

logger = get_logger(__name__)

_VALID_LABELS = frozenset({"kb_qa", "data_query", "clarify"})
_LLM_TRIGGER_REASON_MARKERS = (
    "mixed_",
    "ambiguous_pattern_resolved_by_ctx",
)


def should_invoke_intent_llm(rule: IntentRuleResult, *, conf_threshold: float) -> bool:
    """模式 B 窄触发：规则已给出候选，仅在边界场景调用轻量 LLM。"""
    if rule.intent_confidence < conf_threshold:
        return True
    return any(m in rule.intent_reason for m in _LLM_TRIGGER_REASON_MARKERS)


def _extract_json_obj(text: str) -> Dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _validate_intent_payload(d: Dict[str, Any]) -> tuple[str, float, str] | None:
    label = str(d.get("intent_label") or d.get("label") or "").strip().lower()
    if label not in _VALID_LABELS:
        return None
    try:
        conf = float(d.get("confidence", d.get("intent_confidence", 0.0)))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    reason = str(d.get("reason_zh") or d.get("reason") or "llm_classifier").strip()[:240]
    return label, conf, reason


def _build_intent_llm_messages(
    *,
    query: str,
    history_summary: str,
    rule: IntentRuleResult,
    enable_nl2sql_route: bool,
) -> List[Dict[str, str]]:
    hist = (history_summary or "").strip()
    if len(hist) > 600:
        hist = hist[-600:]
    sys_prompt = (
        "你是电力设备智能客服的意图分类器。只输出一个 JSON 对象，不要其它文字。\n"
        "字段：intent_label（枚举之一：kb_qa、data_query、clarify）、"
        "confidence（0~1）、reason_zh（短句中文理由）。\n\n"
        "标签定义：\n"
        "- kb_qa：概念/机理/标准/故障原因/经验类文档问答，或需要结合知识库解释；\n"
        "- data_query：查台账/统计/列表/检修记录/缺陷单等结构化库表数据；\n"
        "- clarify：过短、指代不清、无法判断用户要什么。\n\n"
        f"NL2SQL 路由是否开启：{enable_nl2sql_route}（关闭时不应输出 data_query）。\n"
        f"规则层初判：{rule.intent_label}，reason={rule.intent_reason}，confidence≈{rule.intent_confidence:.2f}。\n"
        f"会话摘要：{hist or '（无）'}\n"
        f"本轮用户问句：{query.strip()[:800]}\n"
    )
    return [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "请输出 JSON。"},
    ]


async def classify_chatbot_intent_by_llm(
    query: str,
    *,
    enable_nl2sql_route: bool,
    image_urls: List[str],
    history_messages: List[Dict[str, Any]] | None = None,
) -> IntentRuleResult:
    """模式 B：硬规则闸 + rules 主判 + 进程内轻量 LLM 窄触发。"""
    q = (query or "").strip()
    h_sum, prev_task = build_intent_context_from_history(history_messages)
    cfg = get_app_config().chatbot

    def _out(label: str, reason: str, conf: float) -> IntentRuleResult:
        return IntentRuleResult(label, reason, conf, h_sum, prev_task)

    gated = apply_intent_hard_gates(
        q,
        enable_nl2sql_route=enable_nl2sql_route,
        image_urls=image_urls,
        history_summary=h_sum,
        prev_task_type=prev_task,
    )
    if gated is not None:
        return gated

    ruled = classify_chatbot_intent_by_rules(
        query,
        enable_nl2sql_route=enable_nl2sql_route,
        image_urls=image_urls,
        history_messages=history_messages,
    )

    threshold = max(0.0, min(1.0, float(cfg.intent_llm_conf_threshold)))
    if not should_invoke_intent_llm(ruled, conf_threshold=threshold):
        return ruled

    if not enable_nl2sql_route and ruled.intent_label == "data_query":
        return ruled

    messages = _build_intent_llm_messages(
        query=q,
        history_summary=h_sum,
        rule=ruled,
        enable_nl2sql_route=enable_nl2sql_route,
    )
    try:
        runner = ChatbotIntentLocalLlm.get_instance()
        raw = await runner.generate(messages)
        obj = _extract_json_obj(raw)
        if obj is None:
            raise ValueError("intent_llm_json_parse_failed")
        parsed = _validate_intent_payload(obj)
        if parsed is None:
            raise ValueError("intent_llm_validation_failed")
        label, conf, reason_zh = parsed
        if not enable_nl2sql_route and label == "data_query":
            label = "kb_qa"
            reason_zh = f"nl2sql_disabled|{reason_zh}"
        logger.info(
            "chatbot.intent_llm narrow_trigger rule=%s/%s -> llm=%s conf=%.3f",
            ruled.intent_label,
            ruled.intent_reason,
            label,
            conf,
        )
        return _out(label, f"intent_llm|{reason_zh}|rule={ruled.intent_reason}", conf)
    except Exception as e:
        if cfg.intent_llm_fallback_to_rules:
            logger.warning(
                "chatbot.intent_llm failed, fallback rules rule=%s err=%s",
                ruled.intent_reason,
                e,
            )
            return IntentRuleResult(
                ruled.intent_label,
                f"intent_llm_fallback_rules|{ruled.intent_reason}",
                min(ruled.intent_confidence, 0.75),
                ruled.history_summary,
                ruled.prev_task_type,
            )
        logger.exception("chatbot.intent_llm failed and fallback disabled err=%s", e)
        return _out("kb_qa", f"intent_llm_error_default_kb_qa|{type(e).__name__}", 0.5)
