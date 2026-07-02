from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Sequence

from app.rag.models import ChunkRecord, DocumentSource

NS_KB_ENABLED_KEY = "namespace_kb_enabled"
NS_KB_PRIORITY_KEY = "namespace_kb_priority"
DEFAULT_NS_KB_ENABLED = True
DEFAULT_NS_KB_PRIORITY = 1
DEFAULT_NAMESPACE_PATH = "__default__"


def normalize_namespace_kb_priority(priority: int | None) -> int:
    if priority is None:
        return DEFAULT_NS_KB_PRIORITY
    p = int(priority)
    if p < 1:
        raise ValueError("namespace_kb_priority must be >= 1")
    return p


def resolve_namespace_kb_fields(
    enabled: bool | None,
    priority: int | None,
) -> tuple[bool, int]:
    return (
        DEFAULT_NS_KB_ENABLED if enabled is None else bool(enabled),
        normalize_namespace_kb_priority(priority),
    )


def namespace_from_path_param(value: str) -> str | None:
    """URL path 中 ``__default__`` 表示默认分区（与未传 namespace 一致）。"""
    if value in (DEFAULT_NAMESPACE_PATH, "-", "_default"):
        return None
    return value


def chunk_namespace_matches(item_namespace: Any, target_namespace: str | None) -> bool:
    ns = item_namespace
    if target_namespace is None:
        return ns is None or ns == ""
    return ns == target_namespace


def build_namespace_kb_metadata(enabled: bool, priority: int) -> dict[str, Any]:
    return {
        NS_KB_ENABLED_KEY: enabled,
        NS_KB_PRIORITY_KEY: priority,
    }


def merge_doc_metadata_for_record(doc: DocumentSource) -> dict[str, Any]:
    ns_kb = build_namespace_kb_metadata(doc.namespace_kb_enabled, doc.namespace_kb_priority)
    # API / DocumentSource 字段优先于 metadata 内同名字段
    return {**(doc.metadata or {}), **ns_kb}


def build_chunk_metadatas(doc: DocumentSource, chunks: Sequence[ChunkRecord]) -> list[dict[str, Any]]:
    ns_kb = build_namespace_kb_metadata(doc.namespace_kb_enabled, doc.namespace_kb_priority)
    merged_doc_meta = {**(doc.metadata or {}), **ns_kb}
    return [{**merged_doc_meta, **(c.metadata or {}), **ns_kb} for c in chunks]


def apply_namespace_kb_to_document_source(
    doc: DocumentSource,
    *,
    namespace_kb_enabled: bool | None,
    namespace_kb_priority: int | None,
) -> DocumentSource:
    enabled, priority = resolve_namespace_kb_fields(namespace_kb_enabled, namespace_kb_priority)
    doc.namespace_kb_enabled = enabled
    doc.namespace_kb_priority = priority
    return doc


def clone_document_source(doc: DocumentSource, **overrides: Any) -> DocumentSource:
    """复制 DocumentSource 并覆盖指定字段，避免重建时遗漏 namespace_kb_* 等字段。"""
    from dataclasses import replace

    return replace(doc, **overrides)


def parse_kb_enabled_value(val: Any) -> bool | None:
    """解析 metadata 中的启用标志；无法识别时返回 None（视为未设置）。"""
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        normalized = val.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return None


def chunk_passes_kb_enabled_filter(metadata: dict[str, Any] | None) -> bool:
    if not metadata:
        return True
    parsed = parse_kb_enabled_value(metadata.get(NS_KB_ENABLED_KEY))
    if parsed is None:
        return True
    return parsed


def metadata_priority(metadata: dict[str, Any] | None) -> int:
    if not metadata:
        return DEFAULT_NS_KB_PRIORITY
    val = metadata.get(NS_KB_PRIORITY_KEY)
    if val is None:
        return DEFAULT_NS_KB_PRIORITY
    try:
        return int(val)
    except (TypeError, ValueError):
        return DEFAULT_NS_KB_PRIORITY


def apply_priority_score_adjustment(score: float, metadata: dict[str, Any] | None, beta: float) -> float:
    if beta <= 0:
        return score
    priority = metadata_priority(metadata)
    return score - beta * float(priority - 1)


def apply_tiered_priority_order(
    hits: list[dict[str, Any]],
    k_out: int,
    *,
    score_getter: Callable[[dict[str, Any]], float],
) -> list[dict[str, Any]]:
    """按 priority 升序分层，层内保持 score 降序，填满 top_k。"""
    if not hits or k_out <= 0:
        return []
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for hit in hits:
        meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        buckets[metadata_priority(meta)].append(hit)
    out: list[dict[str, Any]] = []
    for priority in sorted(buckets.keys()):
        tier = sorted(buckets[priority], key=score_getter, reverse=True)
        for hit in tier:
            out.append(hit)
            if len(out) >= k_out:
                return out
    return out


def finalize_retrieval_hits(
    hits: list[dict[str, Any]],
    *,
    namespace: str | None,
    priority_boost: float,
    priority_tiered: bool,
    k_out: int,
    score_getter: Callable[[dict[str, Any]], float],
) -> list[dict[str, Any]]:
    """
    过滤禁用 chunk；全库检索时在截断 top_k 前做 priority 排序/分层。
    """
    filtered = [h for h in hits if chunk_passes_kb_enabled_filter(h.get("metadata"))]
    if namespace is not None:
        return filtered[:k_out]

    if priority_tiered:
        return apply_tiered_priority_order(filtered, k_out, score_getter=score_getter)

    if priority_boost <= 0:
        return filtered[:k_out]

    for hit in filtered:
        base = score_getter(hit)
        hit["_priority_adjusted_score"] = apply_priority_score_adjustment(
            base,
            hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {},
            priority_boost,
        )
    filtered.sort(
        key=lambda x: float(x.get("_priority_adjusted_score", score_getter(x))),
        reverse=True,
    )
    return filtered[:k_out]


def build_es_kb_enabled_filter_clause() -> dict[str, Any]:
    """ES filter：启用=true，或字段缺失（兼容旧数据）。"""
    return {
        "bool": {
            "should": [
                {"term": {f"metadata.{NS_KB_ENABLED_KEY}": True}},
                {"bool": {"must_not": [{"exists": {"field": f"metadata.{NS_KB_ENABLED_KEY}"}}]}},
            ],
            "minimum_should_match": 1,
        }
    }


def build_es_namespace_must_clauses(namespace: str | None) -> list[dict[str, Any]]:
    if namespace is None:
        return [
            {
                "bool": {
                    "should": [
                        {"bool": {"must_not": [{"exists": {"field": "namespace"}}]}},
                        {"term": {"namespace": ""}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        ]
    return [{"term": {"namespace": namespace}}]


def build_es_namespace_kb_update_clauses(
    namespace: str | None,
    doc_names: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """
    kb-config 批量更新用的 namespace 匹配（比检索 filter 更宽）：
    - 顶层 namespace 精确匹配；
    - metadata.namespace（含 .keyword 子字段）；
    - docs 索引中登记的 doc_name，且 chunk 顶层 namespace 缺失/为空（常见于历史数据、部分 figure chunk）。
    """
    if namespace is None:
        return build_es_namespace_must_clauses(None)

    ns_should: list[dict[str, Any]] = [
        {"term": {"namespace": namespace}},
        {"term": {"metadata.namespace": namespace}},
        {"term": {"metadata.namespace.keyword": namespace}},
    ]
    outer_should: list[dict[str, Any]] = [
        {"bool": {"should": ns_should, "minimum_should_match": 1}}
    ]
    names = sorted({str(n) for n in (doc_names or []) if n})
    if names:
        outer_should.append(
            {
                "bool": {
                    "must": [
                        {"terms": {"doc_name": names}},
                        {
                            "bool": {
                                "should": [
                                    {"bool": {"must_not": [{"exists": {"field": "namespace"}}]}},
                                    {"term": {"namespace": ""}},
                                ],
                                "minimum_should_match": 1,
                            }
                        },
                    ]
                }
            }
        )
    return [{"bool": {"should": outer_should, "minimum_should_match": 1}}]


def chunk_matches_namespace_kb_target(
    item: dict[str, Any],
    namespace: str | None,
    doc_names: Sequence[str] | None = None,
) -> bool:
    """内存/FAISS 版 kb-config 批量更新匹配（与 build_es_namespace_kb_update_clauses 语义对齐）。"""
    if chunk_namespace_matches(item.get("namespace"), namespace):
        return True
    meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    if namespace is not None and meta.get("namespace") == namespace:
        return True
    if namespace is None:
        return False
    doc_name = item.get("doc_name") or meta.get("doc_name")
    if not doc_name or doc_name not in set(doc_names or []):
        return False
    top_ns = item.get("namespace")
    return top_ns is None or top_ns == ""


def append_es_filters(bool_query: dict[str, Any], extra_filters: list[dict[str, Any]]) -> None:
    if not extra_filters:
        return
    existing = bool_query.get("filter")
    if existing is None:
        bool_query["filter"] = list(extra_filters)
    elif isinstance(existing, list):
        bool_query["filter"] = [*existing, *extra_filters]
    else:
        bool_query["filter"] = [existing, *extra_filters]


def patch_metadata_namespace_kb(
    metadata: dict[str, Any] | None,
    *,
    enabled: bool,
    priority: int,
) -> dict[str, Any]:
    out = dict(metadata or {})
    out[NS_KB_ENABLED_KEY] = enabled
    out[NS_KB_PRIORITY_KEY] = priority
    return out
