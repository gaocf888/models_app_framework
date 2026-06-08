"""
智能客服 RAG 检索范围解析：本厂/该厂等问句锁定电厂专属知识库 namespace。

与 intent 三分流解耦：仅在 kb_qa → RAG 链路内，由 `rag_scope_resolve` 节点写入 state。

默认（``CHATBOT_PLANT_KB_HISTORY_CONTINUATION=false``）仅根据**本轮** user query 判定；
设为 true 时才会扫描近几轮 user 历史做延续锁定。
"""

from __future__ import annotations

from typing import Any, Dict, List, NamedTuple

from app.services.chatbot_image_utils import strip_image_block_from_history

# 厂别/公司/单位指代（命中即锁定 plant_kb_namespace；较长短语与专名类优先列出便于维护）
_PLANT_PRONOUN_MARKERS = (
    # 公司 / 单位
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
    # 电厂 / 厂
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
    # 现场 / 口语
    "本现场",
)

_DEFAULT_PLANT_QUERY_BOOST = "华电五彩湾北一发电有限公司"


class RagScopeResult(NamedTuple):
    """RAG namespace 解析结果。"""

    rag_namespace: str | None
    rag_scope_reason: str
    query_boost: str | None


def _normalize(text: str) -> str:
    return (text or "").replace(" ", "").strip()


def _has_plant_pronoun(text: str) -> bool:
    qn = _normalize(text)
    return any(m in qn for m in _PLANT_PRONOUN_MARKERS)


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
    query_boost_name: str | None = _DEFAULT_PLANT_QUERY_BOOST,
) -> RagScopeResult:
    """
    解析本轮 RAG 是否锁定电厂专属 namespace。

    规则：
    - 本轮含厂别指代 → 锁定 plant_kb_namespace；
    - 当 ``history_continuation=True``（``CHATBOT_PLANT_KB_HISTORY_CONTINUATION``）且
      ``enable_context`` 时：本轮无厂别指代，但近几轮 user 含厂别指代 → 多轮延续锁定；
    - 否则走全库（默认仅看本轮，不扫历史）。
    """
    if not enabled:
        return RagScopeResult(None, "plant_kb_disabled", None)
    ns = (plant_kb_namespace or "").strip()
    if not ns:
        return RagScopeResult(None, "plant_kb_namespace_empty", None)

    q = (query or "").strip()
    if not q:
        return RagScopeResult(None, "empty_query", None)

    boost = (query_boost_name or "").strip() or None

    if _has_plant_pronoun(q):
        return RagScopeResult(ns, "plant_pronoun", boost)

    if history_continuation and enable_context:
        for prev in _recent_user_texts(history_messages):
            if _has_plant_pronoun(prev):
                return RagScopeResult(ns, "plant_pronoun_history_continuation", boost)

    return RagScopeResult(None, "default_all_namespaces", None)


def augment_retrieval_query_for_plant_kb(
    rag_query: str,
    *,
    query_boost: str | None,
) -> str:
    """锁定电厂库时，将电厂正式名称拼入检索句（若尚未出现）。"""
    q = (rag_query or "").strip()
    boost = (query_boost or "").strip()
    if not q or not boost or boost in q:
        return q
    return f"{boost} {q}"
