"""
智能客服 RAG 检索范围解析：地域/组织指代锁定专属知识库 namespace。

与 intent 三分流解耦：仅在 kb_qa → RAG 链路内，由 `rag_scope_resolve` 节点写入 state。

默认（``CHATBOT_PLANT_KB_HISTORY_CONTINUATION=false``）仅根据**本轮** user query 判定；
设为 true 时才会扫描近几轮 user 历史做延续锁定。

markers / namespace / boost 可由 ``CHATBOT_DOMAIN`` 配置包 ``locale_kb`` 注入；
未注入时回退锅炉「本厂」内置词表。
"""

from __future__ import annotations

from typing import Any, Dict, List, NamedTuple, Sequence

from app.services.chatbot_image_utils import strip_image_block_from_history
from app.llm.graphs.chatbot_business_profile import get_chatbot_business_profile

# 锅炉内置「本厂」指代（配置包缺失时兜底）
_BUILTIN_LOCALE_MARKERS = (
    "我们公司",
    "我们单位",
    "我们电厂",
    "我们电站",
    "我们这边",
    "咱们厂",
    "本公司",
    "我公司",
    "我单位",
    "我电厂",
    "本企业",
    "该企业",
    "该单位",
    "本单位",
    "我司",
    "本电厂",
    "该电厂",
    "本电站",
    "该电站",
    "我们厂",
    "本厂",
    "该厂",
    "我厂",
    "咱厂",
    "厂里",
    "这个厂",
    "本锅炉厂",
    "本现场",
)

_DEFAULT_LOCALE_QUERY_BOOST = "华电五彩湾北一发电有限公司"

# 兼容旧名
_PLANT_PRONOUN_MARKERS = _BUILTIN_LOCALE_MARKERS
_DEFAULT_PLANT_QUERY_BOOST = _DEFAULT_LOCALE_QUERY_BOOST


class RagScopeResult(NamedTuple):
    """RAG namespace 解析结果。"""

    rag_namespace: str | None
    rag_scope_reason: str
    query_boost: str | None


def _normalize(text: str) -> str:
    return (text or "").replace(" ", "").strip()


def _active_locale_markers(markers: Sequence[str] | None = None) -> tuple[str, ...]:
    if markers:
        return tuple(m for m in markers if str(m).strip())
    profile_markers = get_chatbot_business_profile().locale_kb.markers
    return profile_markers or _BUILTIN_LOCALE_MARKERS


def _has_locale_pronoun(text: str, markers: Sequence[str] | None = None) -> bool:
    qn = _normalize(text)
    return any(m in qn for m in _active_locale_markers(markers))


# 兼容旧 API
def _has_plant_pronoun(text: str) -> bool:
    return _has_locale_pronoun(text)


def _recent_user_texts(
    history_messages: List[Dict[str, Any]] | None,
    *,
    max_messages: int = 6,
) -> list[str]:
    if not history_messages:
        return []
    out: list[str] = []
    for m in reversed(history_messages):
        if str(m.get("role", "")).lower() != "user":
            continue
        raw = m.get("content", "")
        text = raw if isinstance(raw, str) else str(raw or "")
        plain = strip_image_block_from_history(text).strip()
        if plain:
            out.append(plain)
        if len(out) >= max_messages:
            break
    return list(reversed(out))


def resolve_rag_namespace(
    query: str,
    *,
    enabled: bool,
    plant_kb_namespace: str,
    history_messages: List[Dict[str, Any]] | None = None,
    enable_context: bool = True,
    history_continuation: bool = False,
    query_boost_name: str | None = None,
    locale_markers: Sequence[str] | None = None,
) -> RagScopeResult:
    """
    解析本轮 RAG 是否锁定地域/组织专属 namespace。

    规则：
    - 本轮含 locale 指代 → 锁定 plant_kb_namespace；
    - 当 ``history_continuation=True`` 且 ``enable_context`` 时：近几轮 user 含指代 → 多轮延续锁定；
    - 否则走全库。
    """
    if not enabled:
        return RagScopeResult(None, "locale_kb_disabled", None)
    ns = (plant_kb_namespace or "").strip()
    if not ns:
        return RagScopeResult(None, "locale_kb_namespace_empty", None)

    q = (query or "").strip()
    if not q:
        return RagScopeResult(None, "empty_query", None)

    boost = (query_boost_name or "").strip() or None
    markers = _active_locale_markers(locale_markers)

    if _has_locale_pronoun(q, markers):
        return RagScopeResult(ns, "locale_pronoun", boost)

    if history_continuation and enable_context:
        for prev in _recent_user_texts(history_messages):
            if _has_locale_pronoun(prev, markers):
                return RagScopeResult(ns, "locale_pronoun_history_continuation", boost)

    return RagScopeResult(None, "default_all_namespaces", None)


def augment_retrieval_query_for_plant_kb(
    rag_query: str,
    *,
    query_boost: str | None,
) -> str:
    """锁定专属库时，将正式名称拼入检索句（若尚未出现）。"""
    q = (rag_query or "").strip()
    boost = (query_boost or "").strip()
    if not q or not boost or boost in q:
        return q
    return f"{boost} {q}"


# 别名：逐步迁移到 locale 命名
augment_retrieval_query_for_locale_kb = augment_retrieval_query_for_plant_kb
