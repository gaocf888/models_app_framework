"""

智能客服：模式 B 意图 LLM（规则主判 + 进程内轻量模型窄触发）。



流程：硬规则闸 → rules 主判 → 边界场景 → 本地 Qwen2.5-0.5B-Instruct（CPU）

→ JSON 校验 → 失败回退 rules。



窄触发后拼 prompt 时分场景弱化规则初判，避免小模型被错误规则锚定：

- 低置信触发：不给规则 label/reason，要求独立分类；

- mixed_/指代续问触发：仅弱提示，不给具体规则标签。

"""



from __future__ import annotations



import json

import re

from typing import Any, Dict, List, Literal



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



_VALID_LABELS = frozenset({"kb_qa", "data_query", "clarify", "hybrid_qa"})

_LLM_TRIGGER_REASON_MARKERS = (

    "mixed_",

    "ambiguous_pattern_resolved_by_ctx",

)



IntentLlmTriggerKind = Literal["low_confidence", "mixed", "ambiguous_ctx"]



_INTENT_LLM_EXAMPLES = (

    "分类示例（勿照抄 reason，仅作标签参考）：\n"

    '- data_query：「1号机组管子数量」「#3炉有多少根管」「查询台账里最近一次检修记录」\n'

    '- kb_qa：「过热器爆管常见原因」「锅炉启停注意事项」「什么是蠕变」\n'

    '- hybrid_qa：「查出超温列表并结合规程说明如何处置」\n'

    '- clarify：「这个」「怎么办」（且无足够会话上下文）\n'

)





def should_invoke_intent_llm(rule: IntentRuleResult, *, conf_threshold: float) -> bool:

    """模式 B 窄触发：规则已给出候选，仅在边界场景调用轻量 LLM。"""

    return resolve_intent_llm_trigger(rule, conf_threshold=conf_threshold) is not None





def resolve_intent_llm_trigger(

    rule: IntentRuleResult,

    *,

    conf_threshold: float,

) -> IntentLlmTriggerKind | None:

    """

    解析窄触发原因（用于 prompt 分场景拼装）。



    优先级：指代续问 > 混合意图 > 低置信（避免低置信兜底盖过语义边界提示）。

    """

    threshold = max(0.0, min(1.0, float(conf_threshold)))

    reason = rule.intent_reason or ""

    if "ambiguous_pattern_resolved_by_ctx" in reason:

        return "ambiguous_ctx"

    if "mixed_" in reason:

        return "mixed"

    if rule.intent_confidence < threshold:

        return "low_confidence"

    return None





def _build_rule_hint_for_intent_llm(trigger: IntentLlmTriggerKind) -> str:

    """按触发原因生成给 LLM 的规则侧提示（不传具体 intent_label）。"""

    if trigger == "low_confidence":

        return (

            "规则层对该问句置信不足，请仅根据问句、会话摘要与下列标签定义**独立分类**，"

            "勿沿用或猜测任何规则初判标签。"

        )

    if trigger == "mixed":

        return (

            "规则提示：该问句疑似同时涉及查库数据与文档知识（混合意图）。"

            "若确需「查数 + 结合知识解释/处置」，优先输出 hybrid_qa；"

            "仅当明显偏一边时再选 data_query 或 kb_qa。勿直接照搬规则标签。"

        )

    if trigger == "ambiguous_ctx":

        return (

            "规则提示：该问句疑似指代上文内容的续问，请结合会话摘要判断用户所问；"

            "勿直接照搬规则标签。"

        )

    return ""





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


_INTENT_LLM_DEBUG_RAW_MAX = 800


def _truncate_for_intent_llm_log(text: str, *, max_len: int = _INTENT_LLM_DEBUG_RAW_MAX) -> str:
    s = (text or "").replace("\r\n", "\n").strip()
    if len(s) <= max_len:
        return s
    return f"{s[:max_len]}…(truncated,len={len(s)})"


def _log_intent_llm_inference_debug(
    *,
    trigger: IntentLlmTriggerKind,
    query: str,
    ruled: IntentRuleResult,
    raw: str,
    obj: Dict[str, Any] | None = None,
    parsed: tuple[str, float, str] | None = None,
    outcome: str,
) -> None:
    logger.debug(
        "chatbot.intent_llm inference trigger=%s outcome=%s "
        "rule=%s/%s conf=%.3f query=%r raw=%r parsed_obj=%s parsed=%s",
        trigger,
        outcome,
        ruled.intent_label,
        ruled.intent_reason,
        ruled.intent_confidence,
        query[:120],
        _truncate_for_intent_llm_log(raw),
        obj,
        parsed,
    )


def _build_intent_llm_messages(

    *,

    query: str,

    history_summary: str,

    enable_nl2sql_route: bool,

    trigger: IntentLlmTriggerKind,

) -> List[Dict[str, str]]:

    hist = (history_summary or "").strip()

    if len(hist) > 600:

        hist = hist[-600:]

    rule_hint = _build_rule_hint_for_intent_llm(trigger)

    sys_prompt = (

        "你是电力设备智能客服的意图分类器。只输出一个 JSON 对象，不要其它文字。\n"

        "字段：intent_label（枚举之一：kb_qa、data_query、hybrid_qa、clarify）、"

        "confidence（0~1）、reason_zh（短句中文理由）。\n\n"

        "标签定义：\n"

        "- kb_qa：概念/机理/标准/故障原因/经验类文档问答，或需要结合知识库解释；\n"

        "- data_query：查台账/统计/列表/数量/检修记录/缺陷单等结构化库表数据；\n"

        "- hybrid_qa：既要查库表数据又要结合知识库解释机理/标准/处置；\n"

        "- clarify：过短、指代不清、无法判断用户要什么。\n\n"

        f"{_INTENT_LLM_EXAMPLES}\n"

        f"NL2SQL 路由是否开启：{enable_nl2sql_route}（关闭时不应输出 data_query / hybrid_qa）。\n"

        f"{rule_hint}\n"

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

    trigger = resolve_intent_llm_trigger(ruled, conf_threshold=threshold)

    if trigger is None:

        return ruled



    if not enable_nl2sql_route and ruled.intent_label in {"data_query", "hybrid_qa"}:

        return ruled



    messages = _build_intent_llm_messages(

        query=q,

        history_summary=h_sum,

        enable_nl2sql_route=enable_nl2sql_route,

        trigger=trigger,

    )

    raw = ""
    try:

        runner = ChatbotIntentLocalLlm.get_instance()

        raw = await runner.generate(messages)

        obj = _extract_json_obj(raw)

        if obj is None:

            _log_intent_llm_inference_debug(
                trigger=trigger,
                query=q,
                ruled=ruled,
                raw=raw,
                outcome="json_parse_failed",
            )
            logger.warning(
                "chatbot.intent_llm json_parse_failed trigger=%s rule=%s raw=%r",
                trigger,
                ruled.intent_reason,
                _truncate_for_intent_llm_log(raw),
            )
            raise ValueError("intent_llm_json_parse_failed")

        parsed = _validate_intent_payload(obj)

        if parsed is None:

            _log_intent_llm_inference_debug(
                trigger=trigger,
                query=q,
                ruled=ruled,
                raw=raw,
                obj=obj,
                outcome="validation_failed",
            )
            logger.warning(
                "chatbot.intent_llm validation_failed trigger=%s rule=%s raw=%r obj=%s",
                trigger,
                ruled.intent_reason,
                _truncate_for_intent_llm_log(raw),
                obj,
            )
            raise ValueError("intent_llm_validation_failed")

        label, conf, reason_zh = parsed

        if not enable_nl2sql_route and label in {"data_query", "hybrid_qa"}:

            label = "kb_qa"

            reason_zh = f"nl2sql_disabled|{reason_zh}"

        _log_intent_llm_inference_debug(
            trigger=trigger,
            query=q,
            ruled=ruled,
            raw=raw,
            obj=obj,
            parsed=parsed,
            outcome="ok",
        )
        logger.info(

            "chatbot.intent_llm narrow_trigger trigger=%s rule=%s/%s -> llm=%s conf=%.3f reason=%s raw=%r",

            trigger,

            ruled.intent_label,

            ruled.intent_reason,

            label,

            conf,

            reason_zh,

            _truncate_for_intent_llm_log(raw),

        )

        return _out(label, f"intent_llm|{reason_zh}|rule={ruled.intent_reason}", conf)

    except Exception as e:

        if cfg.intent_llm_fallback_to_rules:

            logger.warning(

                "chatbot.intent_llm failed, fallback rules trigger=%s rule=%s err=%s raw=%r",

                trigger,

                ruled.intent_reason,

                e,

                _truncate_for_intent_llm_log(raw) if raw else "(none)",

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


