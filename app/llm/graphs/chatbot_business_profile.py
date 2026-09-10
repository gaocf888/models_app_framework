"""Chatbot 部署级业务配置包加载（``configs/chatbot_business/<domain>/``）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.logging import get_logger
from app.llm.graphs.chatbot_rag_citations import RAG_CITATIONS_EXCLUDED_NAMESPACES

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_BUSINESS_ROOT = _REPO_ROOT / "configs" / "chatbot_business"

_VALID_DOMAINS = frozenset({"boiler", "subsidence"})
# 别名：subsidence_v1 是 Prompt version，这里映射到业务域 subsidence，二者不是同一开关。
_DOMAIN_ALIASES = {
    "boiler_four_tube": "boiler",
    "boiler_four_tubes": "boiler",
    "djs": "subsidence",
    "subsidence_v1": "subsidence",
}


@dataclass(frozen=True)
class ChatbotLocaleKbConfig:
    """地域/组织锁库（原 plant_kb）配置。"""

    # dataclass 默认 True 是锅炉兜底；地降必须以 yaml enabled: false 覆盖。
    enabled: bool = True
    markers: tuple[str, ...] = ()
    namespace: str = ""
    query_boost: str = ""
    fallback_on_empty: bool = False
    history_continuation: bool = False


@dataclass(frozen=True)
class ChatbotSimilarCaseConfig:
    enabled_default: bool = False
    namespace: str = "事故案例"
    gate_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChatbotBusinessProfile:
    domain: str
    display_name: str = ""
    locale_kb: ChatbotLocaleKbConfig = field(default_factory=ChatbotLocaleKbConfig)
    similar_case: ChatbotSimilarCaseConfig = field(default_factory=ChatbotSimilarCaseConfig)
    data_markers: tuple[str, ...] = ()
    conceptual_markers: tuple[str, ...] = ()
    strong_data_regex: str = ""
    unclear_patterns: tuple[str, ...] = ()
    clarify_reply_snippet: str = ""
    history_continuation_markers: tuple[str, ...] = ()
    clarify_text: str = ""
    unsafe_text: str = ""
    handoff_text: str = ""
    smalltalk_text: str = ""
    follow_up_seeds: tuple[str, ...] = ()
    follow_up_llm_system: str = ""
    topic_follow_ups: dict[str, tuple[str, ...]] = field(default_factory=dict)
    intent_llm_examples: str = ""
    rag_snippet_granularity_forbid: str = ""
    insufficient_evidence_hint: str = ""
    faq_soft_direct_authority: str = ""
    hybrid_synth_constraint: str = ""
    hybrid_query_result_title: str = ""
    hybrid_kb_title: str = ""
    hybrid_both_failed: str = ""
    profile_path: str = ""


def normalize_chatbot_domain(raw: str | None) -> str:
    dom = (raw or "").strip().lower()
    if not dom:
        # 地降所主部署：未配置时默认地面沉降；锅炉部署请显式设 CHATBOT_DOMAIN=boiler
        return "subsidence"
    return _DOMAIN_ALIASES.get(dom, dom)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        s = value.strip()
        return (s,) if s else ()
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for x in value:
            s = str(x or "").strip()
            if s:
                out.append(s)
        return tuple(out)
    return ()


def _as_topic_follow_ups(value: Any) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, cands in value.items():
        topic = str(key or "").strip()
        if not topic:
            continue
        items = _as_str_tuple(cands)
        if items:
            out[topic] = items
    return out


def _resolve_domain(domain: str | None) -> str:
    # 缺省地面沉降（地降所主部署）；锅炉请显式 CHATBOT_DOMAIN=boiler
    return normalize_chatbot_domain(domain or os.getenv("CHATBOT_DOMAIN") or "subsidence")


@lru_cache(maxsize=4)
def get_chatbot_business_profile(domain: str | None = None) -> ChatbotBusinessProfile:
    """
    加载当前部署 domain 对应的 Chatbot 业务配置包。

    ``CHATBOT_DOMAIN`` 缺省为 ``subsidence``（地降所主部署）；
    锅炉部署请在 env 中显式设 ``CHATBOT_DOMAIN=boiler``。
    """
    dom = _resolve_domain(domain)
    if dom not in _VALID_DOMAINS:
        logger.warning("unknown CHATBOT_DOMAIN=%s, fallback to subsidence", dom)
        dom = "subsidence"

    root = _DEFAULT_BUSINESS_ROOT / dom
    profile_raw = _load_yaml(root / "profile.yaml")
    markers_raw = _load_yaml(root / "intent_markers.yaml")
    texts_raw = _load_yaml(root / "clarify_texts.yaml")
    follow_raw = _load_yaml(root / "follow_up.yaml")

    locale_raw = profile_raw.get("locale_kb") if isinstance(profile_raw.get("locale_kb"), dict) else {}
    similar_raw = profile_raw.get("similar_case") if isinstance(profile_raw.get("similar_case"), dict) else {}

    locale = ChatbotLocaleKbConfig(
        enabled=bool(locale_raw.get("enabled", True)),
        markers=_as_str_tuple(locale_raw.get("markers")),
        namespace=str(locale_raw.get("namespace") or "").strip(),
        query_boost=str(locale_raw.get("query_boost") or "").strip(),
        fallback_on_empty=bool(locale_raw.get("fallback_on_empty", False)),
        history_continuation=bool(locale_raw.get("history_continuation", False)),
    )
    similar = ChatbotSimilarCaseConfig(
        enabled_default=bool(similar_raw.get("enabled_default", False)),
        namespace=str(similar_raw.get("namespace") or "事故案例").strip() or "事故案例",
        gate_markers=_as_str_tuple(similar_raw.get("gate_markers")),
    )

    profile = ChatbotBusinessProfile(
        domain=dom,
        display_name=str(profile_raw.get("display_name") or dom).strip(),
        locale_kb=locale,
        similar_case=similar,
        data_markers=_as_str_tuple(markers_raw.get("data_markers")),
        conceptual_markers=_as_str_tuple(markers_raw.get("conceptual_markers")),
        strong_data_regex=str(markers_raw.get("strong_data_regex") or "").strip(),
        unclear_patterns=_as_str_tuple(markers_raw.get("unclear_patterns")),
        clarify_reply_snippet=str(markers_raw.get("clarify_reply_snippet") or "").strip(),
        history_continuation_markers=_as_str_tuple(markers_raw.get("history_continuation_markers")),
        clarify_text=str(texts_raw.get("clarify") or "").strip(),
        unsafe_text=str(texts_raw.get("unsafe") or "").strip(),
        handoff_text=str(texts_raw.get("handoff") or "").strip(),
        smalltalk_text=str(texts_raw.get("smalltalk") or "").strip(),
        follow_up_seeds=_as_str_tuple(follow_raw.get("seeds")),
        follow_up_llm_system=str(follow_raw.get("llm_system") or "").strip(),
        topic_follow_ups=_as_topic_follow_ups(follow_raw.get("topic_follow_ups")),
        intent_llm_examples=str(markers_raw.get("intent_llm_examples") or "").strip(),
        rag_snippet_granularity_forbid=str(texts_raw.get("rag_snippet_granularity_forbid") or "").strip(),
        insufficient_evidence_hint=str(texts_raw.get("insufficient_evidence_hint") or "").strip(),
        faq_soft_direct_authority=str(texts_raw.get("faq_soft_direct_authority") or "").strip(),
        hybrid_synth_constraint=str(texts_raw.get("hybrid_synth_constraint") or "").strip(),
        hybrid_query_result_title=str(texts_raw.get("hybrid_query_result_title") or "").strip(),
        hybrid_kb_title=str(texts_raw.get("hybrid_kb_title") or "").strip(),
        hybrid_both_failed=str(texts_raw.get("hybrid_both_failed") or "").strip(),
        profile_path=str(root / "profile.yaml"),
    )
    logger.info(
        "chatbot business profile loaded domain=%s data_markers=%d conceptual=%d locale_enabled=%s",
        profile.domain,
        len(profile.data_markers),
        len(profile.conceptual_markers),
        profile.locale_kb.enabled,
    )
    return profile


def clear_chatbot_business_profile_cache() -> None:
    get_chatbot_business_profile.cache_clear()


def get_chatbot_retrieve_exclude_namespaces(domain: str | None = None) -> tuple[str, ...]:
    """
    智能问答检索阶段应剔除的 namespace（与引用层 ``RAG_CITATIONS_EXCLUDED_NAMESPACES`` 一致）。

    现网引用组装已过滤三库；此处在 ``retrieve_chunks`` 再排除一次，避免占满 top_k。
    ``domain`` 保留兼容，当前不按域变化。
    """
    _ = domain
    return tuple(sorted(RAG_CITATIONS_EXCLUDED_NAMESPACES))
