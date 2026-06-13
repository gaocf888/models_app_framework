"""
智能客服：将检索分片转为 SSE meta 用的结构化引用列表，以及带编号的 LLM 上下文块。

字段与 `RetrievedChunk` 对齐，便于前端展示「知识来源」；列表顺序与传入 chunks 一致（去重后）。
`ref_index`（从 1 起）与注入 LLM 的 ``[n]`` 编号一致，供正文内联引用解析。
若向量 metadata 中带有摄入时的原始 URL，则 citation 中会包含 `original_content_url`（由前端挂载链接，不写入 LLM prompt）。
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Tuple

from app.rag.models import RetrievedChunk

# 智能客服 finished.meta.rag_citations：不展示 NL2SQL 库表/业务/QA 知识库片段
RAG_CITATIONS_EXCLUDED_NAMESPACES = frozenset(
    {"nl2sql_schema", "nl2sql_biz_knowledge", "nl2sql_qa_examples"}
)


def rag_citation_namespace_allowed(
    namespace: str | None,
    *,
    excluded: frozenset[str] = RAG_CITATIONS_EXCLUDED_NAMESPACES,
) -> bool:
    if not excluded:
        return True
    ns = (namespace or "").strip()
    return ns not in excluded


def filter_rag_citation_dicts(
    citations: List[Dict[str, Any]] | None,
    *,
    excluded: frozenset[str] = RAG_CITATIONS_EXCLUDED_NAMESPACES,
) -> List[Dict[str, Any]]:
    """从已组装的 citation dict 列表中剔除指定 namespace（供结束帧二次过滤）。"""
    if not citations:
        return []
    return [
        c
        for c in citations
        if isinstance(c, dict)
        and rag_citation_namespace_allowed(
            str(c.get("namespace") or "") if c.get("namespace") is not None else None,
            excluded=excluded,
        )
    ]


def _original_content_url_from_chunk_metadata(meta: Any) -> str | None:
    """
    知识摄入时与「正文 content」相关的原始 URL，写入 rag_citations 供前端展示。

    优先级：
    1) content_fetched_from_url：content 为 http(s) 拉取流程写入的原始 URL；
    2) source_uri：切块元数据 / 文档溯源；
    3) 其它常见别名；
    4) metadata.content 若为 http(s) 则视为 URL（兼容少数写入方式）。
    """
    if not isinstance(meta, dict):
        return None
    for key in (
        "content_fetched_from_url",
        "source_uri",
        "source_url",
        "document_url",
        "file_url",
        "content_url",
        "ingest_content_url",
        "original_content_url",
    ):
        v = meta.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    c = meta.get("content")
    if isinstance(c, str) and c.strip():
        s = c.strip()
        low = s.lower()
        if low.startswith("http://") or low.startswith("https://"):
            return s
    return None


def _format_doc_header(chunk: RetrievedChunk) -> str:
    """LLM 可见的文档标签（仅名称 + 章节，不含 URL）。"""
    dn = (chunk.doc_name or "").strip() or "未知文档"
    section = (chunk.section_path or "").strip()
    label = f"《{dn}》"
    if section:
        label += f" {section}"
    return label


def _format_numbered_llm_snippet(ref_index: int, chunk: RetrievedChunk) -> str:
    body = (chunk.text or "").strip()
    return f"[{ref_index}] {_format_doc_header(chunk)}\n{body}"


def _iter_eligible_chunks(
    chunks: List[RetrievedChunk] | None,
    *,
    max_items: int = 24,
    exclude_namespaces: frozenset[str] | None = RAG_CITATIONS_EXCLUDED_NAMESPACES,
) -> Iterator[RetrievedChunk]:
    """与 citations / 编号 LLM 片段共用的过滤与去重迭代器（保序）。"""
    if not chunks:
        return
    max_items = max(1, min(50, max_items))
    seen: set[tuple[str, str, str, str]] = set()
    count = 0
    for c in chunks:
        if not c:
            continue
        if exclude_namespaces is not None and not rag_citation_namespace_allowed(
            getattr(c, "namespace", None), excluded=exclude_namespaces
        ):
            continue
        tx = (c.text or "").strip()
        if not tx:
            continue
        ns = str(c.namespace or "") if c.namespace is not None else ""
        dn = str(c.doc_name or "") if c.doc_name is not None else ""
        cid = str(c.chunk_id or "") if c.chunk_id is not None else ""
        key = (ns, dn, cid, tx[:240])
        if key in seen:
            continue
        seen.add(key)
        yield c
        count += 1
        if count >= max_items:
            break


def chunks_to_rag_context(
    chunks: List[RetrievedChunk] | None,
    *,
    max_items: int = 24,
    exclude_namespaces: frozenset[str] | None = RAG_CITATIONS_EXCLUDED_NAMESPACES,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    同时生成注入 LLM 的编号片段与 ``rag_citations``，保证 ``[n]`` 与 ``ref_index`` 一一对应。

    :return: (numbered_llm_snippets, rag_citations)
    """
    snippets: List[str] = []
    citations: List[Dict[str, Any]] = []
    for ref_index, c in enumerate(
        _iter_eligible_chunks(chunks, max_items=max_items, exclude_namespaces=exclude_namespaces),
        start=1,
    ):
        tx = (c.text or "").strip()
        item: Dict[str, Any] = {
            "ref_index": ref_index,
            "namespace": c.namespace,
            "doc_name": c.doc_name,
            "doc_version": c.doc_version,
            "chunk_id": c.chunk_id,
            "section_path": c.section_path,
            "source": "vector_store",
        }
        if c.score is not None:
            item["score"] = round(float(c.score), 6)
        if getattr(c, "rerank_score", None) is not None:
            item["rerank_score"] = round(float(c.rerank_score), 6)
        if c.pipeline_version:
            item["pipeline_version"] = c.pipeline_version
        preview = tx if len(tx) <= 280 else tx[:277] + "..."
        item["text_preview"] = preview
        orig_url = _original_content_url_from_chunk_metadata(c.metadata)
        if orig_url:
            item["original_content_url"] = orig_url
        citations.append(item)
        snippets.append(_format_numbered_llm_snippet(ref_index, c))
    return snippets, citations


def chunks_to_numbered_llm_snippets(
    chunks: List[RetrievedChunk] | None,
    *,
    max_items: int = 24,
    exclude_namespaces: frozenset[str] | None = RAG_CITATIONS_EXCLUDED_NAMESPACES,
) -> List[str]:
    """将 chunks 转为 ``[n] 《文档名》`` 开头的 LLM 上下文块列表。"""
    snippets, _ = chunks_to_rag_context(
        chunks, max_items=max_items, exclude_namespaces=exclude_namespaces
    )
    return snippets


def chunks_to_rag_citations(
    chunks: List[RetrievedChunk] | None,
    *,
    max_items: int = 24,
    exclude_namespaces: frozenset[str] | None = RAG_CITATIONS_EXCLUDED_NAMESPACES,
) -> List[Dict[str, Any]]:
    """
    将 `RetrievedChunk` 转为可 JSON 序列化的 dict 列表（用于 `finished.meta.rag_citations`）。

    - 按 (namespace, doc_name, chunk_id, text 前缀) 去重，保留首次出现顺序；
    - `ref_index` 从 1 起，与 LLM 正文中的 ``[n]`` 及 ``chunks_to_numbered_llm_snippets`` 对齐；
    - `text_preview` 为片段摘要，避免 meta 过大；
    - 若 chunk 的 metadata 中含摄入原始 URL（见 `_original_content_url_from_chunk_metadata`），则增加 `original_content_url`；
    - 默认排除 ``nl2sql_schema`` / ``nl2sql_biz_knowledge`` / ``nl2sql_qa_examples``（传 ``exclude_namespaces=()`` 可关闭）。
    """
    _, citations = chunks_to_rag_context(
        chunks, max_items=max_items, exclude_namespaces=exclude_namespaces
    )
    return citations
