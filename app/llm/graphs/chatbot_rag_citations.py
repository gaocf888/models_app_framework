"""
智能客服：将检索分片转为 SSE meta 用的结构化引用列表。

字段与 `RetrievedChunk` 对齐，便于前端展示「知识来源」；列表顺序与传入 chunks 一致（去重后）。
若向量 metadata 中带有摄入时的原始 URL，则 citation 中会包含 `original_content_url`。
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.rag.models import RetrievedChunk


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


def chunks_to_rag_citations(chunks: List[RetrievedChunk] | None, *, max_items: int = 24) -> List[Dict[str, Any]]:
    """
    将 `RetrievedChunk` 转为可 JSON 序列化的 dict 列表（用于 `finished.meta.rag_citations`）。

    - 按 (namespace, doc_name, chunk_id, text 前缀) 去重，保留首次出现顺序；
    - `text_preview` 为片段摘要，避免 meta 过大；
    - 若 chunk 的 metadata 中含摄入原始 URL（见 `_original_content_url_from_chunk_metadata`），则增加 `original_content_url`。
    """
    if not chunks:
        return []
    max_items = max(1, min(50, max_items))
    seen: set[tuple[str, str, str, str]] = set()
    out: List[Dict[str, Any]] = []
    for c in chunks:
        if not c:
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
        item: Dict[str, Any] = {
            "namespace": c.namespace,
            "doc_name": c.doc_name,
            "doc_version": c.doc_version,
            "chunk_id": c.chunk_id,
            "section_path": c.section_path,
            "source": "vector_store",
        }
        if c.score is not None:
            item["score"] = round(float(c.score), 6)
        if c.pipeline_version:
            item["pipeline_version"] = c.pipeline_version
        preview = tx if len(tx) <= 280 else tx[:277] + "..."
        item["text_preview"] = preview
        orig_url = _original_content_url_from_chunk_metadata(c.metadata)
        if orig_url:
            item["original_content_url"] = orig_url
        out.append(item)
        if len(out) >= max_items:
            break
    return out
