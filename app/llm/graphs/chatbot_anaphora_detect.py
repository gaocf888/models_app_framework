"""
§3.2 / P0：规则层指代检测（输出封闭枚举 + 置信度 + 分差，供 P3 窄触发）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from app.llm.graphs.chatbot_anaphora_config import AnaphoraRuntimeConfig, get_anaphora_runtime_config
from app.llm.graphs.chatbot_anaphora_types import AnaphoraType


@dataclass(frozen=True)
class AnaphoraRuleResult:
    anaphora_type: str
    confidence: float
    score_gap: float
    second_type: str
    scores: Dict[str, float]


_TYPE_PRIORITY: tuple[str, ...] = (
    AnaphoraType.META_CONFIRM.value,
    AnaphoraType.PAIR_COMPARE.value,
    AnaphoraType.ORDINAL.value,
    AnaphoraType.CONTINUATION.value,
    AnaphoraType.ELLIPSIS.value,
    AnaphoraType.SINGLE_ENTITY.value,
)


def _rank_key(item: Tuple[str, float]) -> Tuple[float, int]:
    code, sc = item
    pri = _TYPE_PRIORITY.index(code) if code in _TYPE_PRIORITY else 99
    return (-sc, pri)


def _norm_q(query: str) -> str:
    return (query or "").strip().replace(" ", "").replace("\n", "")


def _score_type(code: str, q_norm: str, q_raw: str, row_keywords: tuple[str, ...], row_regex: tuple[str, ...]) -> float:
    s = 0.0
    for kw in row_keywords:
        if kw and kw in q_norm:
            s += 1.0 + min(2.0, len(kw) / 12.0)
    for pat in row_regex:
        if not pat:
            continue
        try:
            if re.search(pat, q_raw):
                s += 2.5
        except re.error:
            continue
    return s


def _meta_confirm_extra(q_norm: str, q_raw: str, hist_len: int, max_chars: int) -> float:
    s = 0.0
    if hist_len <= 0:
        return 0.0
    if len(q_norm) > max_chars:
        return 0.0
    if len(q_norm) <= 14 and q_norm.endswith(("吗", "么", "嘛")):
        s += 0.8
    if len(q_norm) <= 10 and "确定" in q_norm:
        s += 1.2
    return s


def _ellipsis_extra(
    q_norm: str,
    q_raw: str,
    hist_len: int,
    max_chars: int,
    keywords: tuple[str, ...],
) -> float:
    if hist_len <= 0 or len(q_norm) > max_chars:
        return 0.0
    hit = any(k in q_norm for k in keywords)
    if not hit:
        return 0.0
    if len(q_raw) > max_chars + 8:
        return 0.0
    return 1.2


def classify_anaphora_rules(
    query: str,
    history_messages: List[Dict[str, Any]] | None,
    *,
    enable_context: bool = True,
    config_path: str | None = None,
) -> AnaphoraRuleResult:
    arc = get_anaphora_runtime_config(config_path)
    hist = list(history_messages or [])
    if not enable_context or not hist:
        return AnaphoraRuleResult(
            anaphora_type=AnaphoraType.NONE.value,
            confidence=1.0,
            score_gap=1.0,
            second_type=AnaphoraType.NONE.value,
            scores={AnaphoraType.NONE.value: 1.0},
        )

    q_raw = (query or "").strip()
    qn = _norm_q(q_raw)
    scores: Dict[str, float] = {}
    th = arc.thresholds
    meta_max = int(th.get("meta_confirm_max_chars", 36))
    ell_max = int(th.get("ellipsis_max_chars", 16))
    se_max = int(th.get("single_entity_max_chars", 40))

    for code, row in arc.types.items():
        if code == AnaphoraType.NONE.value:
            continue
        base = _score_type(code, qn, q_raw, row.keywords, row.regex)
        if code == AnaphoraType.META_CONFIRM.value:
            base += _meta_confirm_extra(qn, q_raw, len(hist), meta_max)
        if code == AnaphoraType.ELLIPSIS.value:
            row_e = arc.types.get(AnaphoraType.ELLIPSIS.value)
            kws = row_e.keywords if row_e else ()
            base += _ellipsis_extra(qn, q_raw, len(hist), ell_max, kws)
        if code == AnaphoraType.SINGLE_ENTITY.value:
            if len(q_raw) > se_max:
                base = 0.0
            elif not re.match(r"^(它|这个|那个|该|此|上述|前面说的)", q_raw):
                base = 0.0
        scores[code] = float(base)

    ranked: List[Tuple[str, float]] = sorted(scores.items(), key=_rank_key)
    best_t, best_s = ranked[0]
    second_t, second_s = ranked[1] if len(ranked) > 1 else (AnaphoraType.NONE.value, 0.0)

    if best_s <= 0.0:
        return AnaphoraRuleResult(
            anaphora_type=AnaphoraType.NONE.value,
            confidence=1.0,
            score_gap=1.0,
            second_type=AnaphoraType.NONE.value,
            scores=dict(scores),
        )

    row_best = arc.types.get(best_t)
    if row_best and not row_best.p0_retrieval_fusion:
        return AnaphoraRuleResult(
            anaphora_type=AnaphoraType.NONE.value,
            confidence=1.0,
            score_gap=max(0.0, best_s - second_s),
            second_type=best_t,
            scores=dict(scores),
        )

    denom = best_s + second_s + 1e-6
    confidence = float(best_s / denom)
    gap = float(best_s - second_s)
    return AnaphoraRuleResult(
        anaphora_type=best_t,
        confidence=confidence,
        score_gap=gap,
        second_type=second_t,
        scores=dict(scores),
    )


def should_fuse_retrieval_for_type(anaphora_type: str, arc: AnaphoraRuntimeConfig) -> bool:
    row = arc.types.get(anaphora_type)
    return bool(row and row.p0_retrieval_fusion)
