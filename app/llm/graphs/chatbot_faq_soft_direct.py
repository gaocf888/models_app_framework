"""
智能客服：高分 FAQ 软直通（生成阶段）。

目的
----
当检索首条 citation 与用户问题高度匹配（如「1000问」类问答库）且本轮无指代续问时，
在 **生成拼消息** 阶段不注入多轮 history_messages，避免旧 assistant 回答把模型带偏；
检索阶段（指代检测、rag_query 融合、namespace 锁定）不受影响。

触发条件（须全部满足，见 ``evaluate_faq_soft_direct``）
----
- 配置 ``CHATBOT_FAQ_SOFT_DIRECT_ENABLED=true``（默认开）；
- ``enable_rag`` 且 ``intent_label == kb_qa``；
- ``anaphora_type == none`` 且 ``anaphora_rule_type == none``（避免 single_entity 等被压成 none 的误判）；
- 用户问句不以常见指代词开头（这个/该/上述/它…）；
- ``rag_citations[0].score >= CHATBOT_FAQ_SOFT_DIRECT_MIN_SCORE``（默认 0.95）；
- 首条 citation 有非空 ``text_preview``，且存在至少一条 context_snippet。

软直通行为
----------
- ``kb_build_messages`` / legacy ``_build_llm_messages``：**不注入** history_messages；
- 注入 LLM 的片段数裁为 ``CHATBOT_FAQ_SOFT_DIRECT_SNIPPET_TOP_N``（默认 1）；
- system 追加「权威片段」说明；``rag_citations`` 展示条数不变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from app.llm.graphs.chatbot_anaphora_types import AnaphoraType

# 问句以这些指代开头时，即使 anaphora_type=none 也不走软直通（防御 single_entity 被折叠为 none）。
_ANAPHORA_QUERY_PREFIX_RE = re.compile(
    r"^\s*(它|这个|那个|该|此|上述|前面说的|前面|刚才|上一轮)",
)

_FAQ_SOFT_DIRECT_AUTHORITY_BLOCK = (
    "【高分 FAQ 软直通·权威片段】下列第一条知识片段与本轮用户问题高度匹配（检索分达到软直通阈值）。"
    "回答时必须优先逐条复述该条片段中的问答内容，不得采用对话历史中关于同一问题的旧结论，"
    "不得改写成泛化的「燃烧优化/安全监控」等宏观框架，除非该片段本身如此表述。"
)


@dataclass(frozen=True)
class FaqSoftDirectDecision:
    """软直通判定结果；``active=True`` 时生成阶段应跳过 history_messages。"""

    active: bool
    reason: str


def query_starts_with_anaphora_marker(query: str) -> bool:
    """用户问句是否以显式指代词开头（软直通安全闸）。"""
    return bool(_ANAPHORA_QUERY_PREFIX_RE.search((query or "").strip()))


def _top_citation_score(rag_citations: Sequence[Dict[str, Any]] | None) -> float | None:
    if not rag_citations:
        return None
    first = rag_citations[0]
    if not isinstance(first, dict):
        return None
    raw = first.get("score")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def evaluate_faq_soft_direct(
    *,
    enabled: bool,
    min_score: float,
    enable_rag: bool,
    intent_label: str | None,
    anaphora_type: str | None,
    anaphora_rule_type: str | None,
    query: str,
    rag_citations: Sequence[Dict[str, Any]] | None,
    context_snippets: Sequence[str] | None,
) -> FaqSoftDirectDecision:
    """
    判定本轮是否启用 FAQ 软直通（仅影响生成上下文，不改检索结果）。

    :return: ``FaqSoftDirectDecision``；``reason`` 供日志与 finished.meta 观测。
    """
    if not enabled:
        return FaqSoftDirectDecision(False, "disabled")
    if not enable_rag:
        return FaqSoftDirectDecision(False, "enable_rag_false")
    if (intent_label or "kb_qa").strip().lower() != "kb_qa":
        return FaqSoftDirectDecision(False, f"intent_not_kb_qa:{intent_label}")
    if (anaphora_type or AnaphoraType.NONE.value).strip() != AnaphoraType.NONE.value:
        return FaqSoftDirectDecision(False, f"anaphora_type:{anaphora_type}")
    if (anaphora_rule_type or AnaphoraType.NONE.value).strip() != AnaphoraType.NONE.value:
        return FaqSoftDirectDecision(False, f"anaphora_rule_type:{anaphora_rule_type}")
    if query_starts_with_anaphora_marker(query):
        return FaqSoftDirectDecision(False, "query_anaphora_prefix")
    snippets = [str(s).strip() for s in (context_snippets or []) if str(s).strip()]
    if not snippets:
        return FaqSoftDirectDecision(False, "no_context_snippets")
    score = _top_citation_score(rag_citations)
    if score is None:
        return FaqSoftDirectDecision(False, "no_top_citation_score")
    if score < float(min_score):
        return FaqSoftDirectDecision(False, f"score_below_threshold:{score:.4f}<{min_score}")
    cites = list(rag_citations or [])
    if cites and isinstance(cites[0], dict):
        preview = str(cites[0].get("text_preview") or "").strip()
        if not preview:
            return FaqSoftDirectDecision(False, "empty_top_citation_preview")
    return FaqSoftDirectDecision(True, f"active:score={score:.4f}")


def snippets_for_llm_generation(
    context_snippets: Sequence[str] | None,
    *,
    soft_direct: bool,
    snippet_top_n: int,
) -> List[str]:
    """软直通时仅取前 N 条片段注入 LLM；非软直通返回原列表副本。"""
    items = [str(s).strip() for s in (context_snippets or []) if str(s).strip()]
    if not soft_direct:
        return items
    n = max(1, min(10, int(snippet_top_n)))
    return items[:n]


def format_rag_snippets_for_generation(
    context_snippets: Sequence[str],
    *,
    soft_direct: bool,
    base_formatter,
) -> str:
    """
    拼装注入 system 的 RAG 片段块。

    ``base_formatter`` 为 ``format_rag_snippets_system_block``，避免循环导入。
    """
    body = base_formatter(list(context_snippets))
    if not soft_direct:
        return body
    return f"{_FAQ_SOFT_DIRECT_AUTHORITY_BLOCK}\n{body}"
