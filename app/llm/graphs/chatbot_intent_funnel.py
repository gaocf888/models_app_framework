"""
智能客服意图漏斗：L1 规则 → L2 Embedding 原型匹配 → L3 vLLM 主对话短提示。

嵌入复用 EmbeddingService；L3 复用 VLLMHttpClient（主对话模型），不使用 0.5B/BERT。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.llm.client import VLLMHttpClient
from app.llm.graphs.chatbot_business_profile import get_chatbot_business_profile
from app.llm.graphs.chatbot_intent_prototypes import (
    DEFAULT_SIM_ACCEPT,
    DEFAULT_SIM_MARGIN,
    match_intent_by_prototypes,
)
from app.llm.graphs.chatbot_intent_rules import (
    IntentRuleResult,
    classify_chatbot_intent_by_rules,
)
from app.rag.embedding_service import EmbeddingService

logger = get_logger(__name__)

_VALID_LABELS = frozenset({"kb_qa", "data_query", "clarify", "hybrid_qa"})

# L1：明确启发式可短路（避免无谓 L2/L3）
_L1_SHORT_CIRCUIT_REASONS = frozenset(
    {
        "structured_query_heuristic",
        "conceptual_qa_heuristic",
    }
)
_L1_HARD_GATE_PREFIXES = (
    "empty_query",
    "query_too_short",
    "ambiguous_query_pattern",
    "ambiguous_pattern_resolved_by_ctx",
    "nl2sql_route_disabled",
    "has_images_default_kb_qa",
    "short_followup_continues_thread",
)

_L1_CONF_SHORT_CIRCUIT = 0.78
_L3_MAX_TOKENS = 64
_L3_TIMEOUT_SEC = 15.0


def _rules_should_short_circuit(ruled: IntentRuleResult) -> bool:
    reason = (ruled.intent_reason or "").strip()
    if any(reason.startswith(p) for p in _L1_HARD_GATE_PREFIXES):
        return True
    if reason in _L1_SHORT_CIRCUIT_REASONS and ruled.intent_confidence >= _L1_CONF_SHORT_CIRCUIT:
        return True
    # default_kb_qa / mixed_hybrid / 低置信 → 必须下沉
    if "default_kb_qa" in reason or "mixed_" in reason:
        return False
    return ruled.intent_confidence >= 0.9


def _clamp_label(label: str, *, enable_nl2sql_route: bool) -> str:
    lab = (label or "").strip()
    if lab not in _VALID_LABELS:
        return "kb_qa"
    if not enable_nl2sql_route and lab in {"data_query", "hybrid_qa"}:
        return "kb_qa"
    return lab


def _allowed_labels() -> frozenset[str]:
    raw = get_app_config().chatbot.intent_output_labels or []
    allowed = {str(x).strip() for x in raw if str(x).strip()}
    return frozenset(allowed & _VALID_LABELS) or _VALID_LABELS


def _extract_json_obj(text: str) -> Dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _funnel_examples() -> str:
    custom = (get_chatbot_business_profile().intent_llm_examples or "").strip()
    if custom:
        return custom
    return (
        '- data_query：「统计朝阳区年沉降」「哪些行政区近期沉降相对偏大」\n'
        '- kb_qa：「分层标与基岩标差异是什么」「地面沉降成因」\n'
        '- hybrid_qa：「查出沉降偏大的点并结合规范说明」\n'
        '- clarify：「这个呢」「怎么办」\n'
    )


def _build_l3_messages(query: str, *, enable_nl2sql_route: bool, l2_hint: str) -> List[Dict[str, str]]:
    labels = ",".join(sorted(_allowed_labels()))
    sys = (
        "你是意图分类器。只输出一个JSON对象，不要其它文字。"
        f'字段：intent_label（{labels}）、confidence（0~1）、reason（短）。'
        "data_query=查监测/台账等结构化数据；kb_qa=概念机理规范；"
        "hybrid_qa=查数+解释；clarify=过短或指代不清。"
        f"NL2SQL路由={'开' if enable_nl2sql_route else '关（勿输出data_query/hybrid_qa）'}。"
        f"示例：\n{_funnel_examples()}"
        f"L2提示：{l2_hint}"
    )
    return [
        {"role": "system", "content": sys},
        {"role": "user", "content": f"问句：{(query or '').strip()[:500]}\n请输出JSON。"},
    ]


async def _l3_classify(
    query: str,
    *,
    enable_nl2sql_route: bool,
    l2_hint: str,
    llm_client: VLLMHttpClient | None,
) -> tuple[str, float, str] | None:
    llm = llm_client or VLLMHttpClient(timeout=_L3_TIMEOUT_SEC)
    model = get_app_config().llm.default_model
    messages = _build_l3_messages(query, enable_nl2sql_route=enable_nl2sql_route, l2_hint=l2_hint)
    try:
        raw = await llm.chat(
            model=model,
            messages=messages,
            max_tokens=_L3_MAX_TOKENS,
            temperature=0.0,
            timeout=_L3_TIMEOUT_SEC,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("chatbot.intent_funnel l3 failed err=%s", e)
        return None

    obj = _extract_json_obj(raw)
    if not obj:
        logger.warning("chatbot.intent_funnel l3 json_parse_failed raw=%r", (raw or "")[:200])
        return None
    label = _clamp_label(str(obj.get("intent_label") or ""), enable_nl2sql_route=enable_nl2sql_route)
    if label not in _allowed_labels():
        label = "kb_qa"
    try:
        conf = float(obj.get("confidence", 0.7))
    except (TypeError, ValueError):
        conf = 0.7
    conf = max(0.0, min(1.0, conf))
    reason = str(obj.get("reason") or "l3_vllm").strip()[:80]
    return label, conf, reason


async def classify_chatbot_intent_by_funnel(
    query: str,
    *,
    enable_nl2sql_route: bool,
    image_urls: List[str],
    history_messages: List[Dict[str, Any]] | None = None,
    embedding_service: EmbeddingService | None = None,
    llm_client: VLLMHttpClient | None = None,
    sim_accept: float = DEFAULT_SIM_ACCEPT,
    sim_margin: float = DEFAULT_SIM_MARGIN,
) -> IntentRuleResult:
    """L1 rules → L2 prototypes → L3 vLLM；任一层失败可回退规则结果。"""
    ruled = classify_chatbot_intent_by_rules(
        query,
        enable_nl2sql_route=enable_nl2sql_route,
        image_urls=image_urls,
        history_messages=history_messages,
    )

    def _wrap(label: str, reason: str, conf: float) -> IntentRuleResult:
        return IntentRuleResult(
            _clamp_label(label, enable_nl2sql_route=enable_nl2sql_route),
            reason,
            conf,
            ruled.history_summary,
            ruled.prev_task_type,
        )

    if _rules_should_short_circuit(ruled):
        return _wrap(
            ruled.intent_label,
            f"funnel_l1_rules|{ruled.intent_reason}",
            ruled.intent_confidence,
        )

    # L2
    match = match_intent_by_prototypes(
        query,
        embedding_service=embedding_service,
        sim_accept=sim_accept,
        sim_margin=sim_margin,
    )
    if match.accepted and match.label:
        return _wrap(
            match.label,
            f"funnel_l2_embed|{match.reason}",
            max(0.55, min(0.95, float(match.best_score))),
        )

    l2_hint = match.reason or "l2_unavailable"
    if match.label:
        l2_hint = f"{l2_hint}|suggest={match.label}"

    # L3
    l3 = await _l3_classify(
        query,
        enable_nl2sql_route=enable_nl2sql_route,
        l2_hint=l2_hint,
        llm_client=llm_client,
    )
    if l3 is not None:
        label, conf, reason = l3
        logger.info(
            "chatbot.intent_funnel l3 hit label=%s conf=%.3f rule=%s l2=%s",
            label,
            conf,
            ruled.intent_label,
            l2_hint,
        )
        return _wrap(label, f"funnel_l3_vllm|{reason}|l2={l2_hint}", conf)

    return _wrap(
        ruled.intent_label,
        f"funnel_l3_fail_fallback_rules|{ruled.intent_reason}|l2={l2_hint}",
        ruled.intent_confidence,
    )
