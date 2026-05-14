"""
§4.4 P3：窄触发 Coref LLM + 短时缓存（本版不做思考流并行，见方案 §4.4.3）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from app.core.logging import get_logger
from app.core.metrics import (
    ANAPHORA_COREF_CACHE_HIT_COUNT,
    ANAPHORA_COREF_CACHE_MISS_COUNT,
    ANAPHORA_LLM_CALL_COUNT,
    ANAPHORA_LLM_FALLBACK_COUNT,
)
from app.llm.client import VLLMHttpClient
from app.llm.graphs.chatbot_anaphora_config import get_anaphora_runtime_config
from app.llm.graphs.chatbot_anaphora_detect import AnaphoraRuleResult
from app.llm.graphs.chatbot_anaphora_store import coref_cache_get, coref_cache_key, coref_cache_set
from app.llm.graphs.chatbot_anaphora_types import ANAPHORA_TYPE_CODES, AnaphoraType
from app.services.chatbot_image_utils import strip_image_block_from_history

logger = get_logger(__name__)


def _last_assistant_tail(history_messages: List[Dict[str, Any]], tail_chars: int) -> str:
    for m in reversed(history_messages or []):
        if str(m.get("role", "") or "").lower() != "assistant":
            continue
        c = m.get("content", "")
        t = c if isinstance(c, str) else str(c or "")
        p = strip_image_block_from_history(t).strip()
        if p:
            return p[-tail_chars:] if len(p) > tail_chars else p
    return ""


def _narrow_trigger(
    rule: AnaphoraRuleResult,
    *,
    tau: float,
    delta: float,
    disambiguation_types: frozenset[str],
    enable_context: bool,
    history_nonempty: bool,
) -> bool:
    """§4.4.1：T1/T5 由调用方保证；此处实现 T2/T3/(T4∧(T2∨T3)) 合成。"""
    if not enable_context or not history_nonempty:
        return False
    t2 = rule.confidence < tau
    t3 = rule.score_gap < delta
    t4 = rule.anaphora_type in disambiguation_types
    # 文档合成：T1∧T5∧(T2∨T3∨(T4∧(T2∨T3))) = T2∨T3（布尔吸收）
    inner = t2 or t3 or (t4 and (t2 or t3))
    return bool(inner)


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


def _validate_coref_payload(d: Dict[str, Any]) -> tuple[str, float, list[int]] | None:
    t = str(d.get("type") or d.get("anaphora_type") or "").strip()
    if t not in ANAPHORA_TYPE_CODES:
        return None
    try:
        conf = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    idx = d.get("anchor_indices")
    out_idx: list[int] = []
    if isinstance(idx, list):
        for x in idx:
            try:
                out_idx.append(int(x))
            except (TypeError, ValueError):
                continue
    return t, conf, out_idx


async def maybe_apply_coref_llm(
    llm: VLLMHttpClient | None,
    *,
    user_id: str,
    session_id: str,
    query: str,
    history_messages: List[Dict[str, Any]],
    rule: AnaphoraRuleResult,
    enable_context: bool,
    llm_gate_enabled: bool,
    config_path: str | None,
    model_name: str | None,
    timeout_sec: float,
) -> tuple[str, str]:
    """
    :return: (final_anaphora_type, source) source ∈ {rule, cache, llm}
    """
    arc = get_anaphora_runtime_config(config_path)
    p3 = arc.p3
    hist = list(history_messages or [])
    if not llm_gate_enabled or llm is None:
        return rule.anaphora_type, "rule"
    if not _narrow_trigger(
        rule,
        tau=p3.tau,
        delta=p3.delta,
        disambiguation_types=p3.disambiguation_types,
        enable_context=enable_context,
        history_nonempty=bool(hist),
    ):
        return rule.anaphora_type, "rule"

    tail = _last_assistant_tail(hist, p3.assistant_tail_hash_chars)
    ck = coref_cache_key(
        user_id,
        session_id,
        query,
        tail,
        lowercase=p3.query_normalize_lowercase,
        tail_chars=p3.assistant_tail_hash_chars,
    )
    cached = coref_cache_get(ck)
    if cached:
        parsed = _validate_coref_payload(cached)
        if parsed:
            ANAPHORA_COREF_CACHE_HIT_COUNT.inc()
            t, _, _ = parsed
            return t, "cache"
    ANAPHORA_COREF_CACHE_MISS_COUNT.inc()

    u_last, a_last = "", ""
    for m in reversed(hist):
        role = str(m.get("role", "") or "").lower()
        c = m.get("content", "")
        txt = c if isinstance(c, str) else str(c or "")
        plain = strip_image_block_from_history(txt).strip()
        if not plain:
            continue
        if role == "user" and not u_last:
            u_last = plain[:800]
        elif role == "assistant" and not a_last:
            a_last = plain[:1200]
        if u_last and a_last:
            break

    sys_prompt = (
        "你是中文对话指代分类器。只输出一个 JSON 对象，不要其它文字。"
        "字段：type（必须在下列枚举之一）"
        f"{sorted(ANAPHORA_TYPE_CODES)}，"
        "confidence（0~1），anchor_indices（整数数组，可空），rationale_zh（短句）。\n"
        f"规则层初判：{rule.anaphora_type}，置信度约 {rule.confidence:.2f}，与第二名分差 {rule.score_gap:.2f}。\n"
        f"上轮用户：{u_last or '（无）'}\n"
        f"上轮助手：{a_last or '（无）'}\n"
        f"本轮用户：{query.strip()[:800]}\n"
    )
    ANAPHORA_LLM_CALL_COUNT.inc()
    try:
        text = await llm.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": "请输出 JSON。"},
            ],
            max_tokens=256,
            temperature=0.0,
            timeout=max(1.0, float(timeout_sec)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("anaphora coref llm fallback: %s", exc)
        ANAPHORA_LLM_FALLBACK_COUNT.inc()
        return rule.anaphora_type, "rule"

    payload = _extract_json_obj(text)
    if not payload:
        ANAPHORA_LLM_FALLBACK_COUNT.inc()
        return rule.anaphora_type, "rule"
    parsed = _validate_coref_payload(payload)
    if not parsed:
        ANAPHORA_LLM_FALLBACK_COUNT.inc()
        return rule.anaphora_type, "rule"
    t, conf, _ = parsed
    if conf < 0.35:
        ANAPHORA_LLM_FALLBACK_COUNT.inc()
        return rule.anaphora_type, "rule"
    coref_cache_set(ck, {"type": t, "confidence": conf, "anchor_indices": payload.get("anchor_indices")}, p3.coref_cache_ttl_sec)
    return t, "llm"
