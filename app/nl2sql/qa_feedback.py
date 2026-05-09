"""
NL2SQL 向量库问答闭环：自动写入校验通过的 Q→SQL，并在检索侧按数据源/schema 指纹过滤。

见 AnalysisConfig.nl2sql_qa_feedback_enabled / NL2SQL_QA_FILTER_ENABLED。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger
from app.nl2sql.rag_service import NL2SQLRAGService
from app.nl2sql.sql_cache import normalize_nl2sql_question
from app.rag.models import RetrievedChunk
from app.rag.rag_service import RAGService

logger = get_logger(__name__)

# 元数据：与人工摄入区分，并支撑检索过滤
NL2SQL_QA_AUTO_KIND = "nl2sql_system_feedback_v1"
NL2SQL_QA_DOC_VERSION_AUTO = "auto_v1"
META_KEY_AUTO_KIND = "nl2sql_auto_kind"
META_KEY_INGEST_SOURCE = "ingest_source"
INGEST_SOURCE_AUTO = "auto"
META_KEY_DATA_SOURCE_FP = "data_source_fp"
META_KEY_SCHEMA_FP = "schema_fp"
META_KEY_POLICY_FP = "policy_fp"


@dataclass(frozen=True)
class NL2SQLQARetrievalContext:
    """当前请求上下文：仅保留与自动写入时一致的 QA 片段。"""

    data_source_fp: str
    schema_fp: str
    policy_fp: str
    analysis_type: str | None = None


def build_nl2sql_auto_qa_doc_name(
    *,
    data_source_fp: str,
    schema_fp: str,
    policy_fp: str,
    analysis_type: str | None,
    plan_item_id: str | None,
    question: str,
) -> str:
    qn = normalize_nl2sql_question(question)
    raw = (
        f"{data_source_fp}\0{schema_fp}\0{policy_fp}\0"
        f"{(analysis_type or '').strip()}\0{(plan_item_id or '').strip()}\0{qn}"
    )
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"nl2sql_auto_{h}"


def format_nl2sql_qa_embedding_text(
    *,
    question: str,
    sql: str,
    prompt_prefix_snapshot: str | None,
    max_prefix_chars: int = 6000,
) -> str:
    """写入向量库的单一文本：检索侧用语义匹配「问题 + SQL 模式 + 提示前缀摘要」。"""
    prefix = (prompt_prefix_snapshot or "").strip()
    if len(prefix) > max_prefix_chars:
        prefix = prefix[: max_prefix_chars - 3] + "..."
    parts = [
        "【NL2SQL 问答样例·系统自动写入】",
        f"【用户问题】{question.strip()}",
    ]
    if prefix:
        parts.append(f"【预制提示前缀摘要】\n{prefix}")
    parts.append(f"【校验通过的 SQL】\n{sql.strip()}")
    return "\n\n".join(parts)


def qa_chunk_passes_retrieval_filter(
    chunk: RetrievedChunk,
    ctx: NL2SQLQARetrievalContext | None,
) -> bool:
    """
    仅对 nl2sql_qa_examples 命名空间生效；schema/biz 始终放行（调用方应只对 QA  chunk 调用）。
    """
    if ctx is None:
        return True
    ns = chunk.namespace or ""
    if ns != NL2SQLRAGService.NS_QA:
        return True

    meta = chunk.metadata if isinstance(chunk.metadata, dict) else {}
    include_legacy = os.getenv("NL2SQL_QA_INCLUDE_LEGACY_UNSCOPED", "true").lower() == "true"

    kind = str(meta.get(META_KEY_AUTO_KIND) or "")
    src = str(meta.get(META_KEY_INGEST_SOURCE) or "")

    # 系统自动写入：必须指纹全匹配
    if kind == NL2SQL_QA_AUTO_KIND or src == INGEST_SOURCE_AUTO:
        if meta.get(META_KEY_DATA_SOURCE_FP) != ctx.data_source_fp:
            return False
        if meta.get(META_KEY_SCHEMA_FP) != ctx.schema_fp:
            return False
        pol_m = meta.get(META_KEY_POLICY_FP)
        if ctx.policy_fp and pol_m not in (None, "") and str(pol_m) != str(ctx.policy_fp):
            return False
        at_m = meta.get("analysis_type")
        if ctx.analysis_type and at_m not in (None, "") and str(at_m) != str(ctx.analysis_type):
            return False
        return True

    # 显式打了指纹的人工/旧版文档：按指纹约束
    ds = meta.get(META_KEY_DATA_SOURCE_FP)
    sf = meta.get(META_KEY_SCHEMA_FP)
    if ds or sf:
        return str(ds or "") == ctx.data_source_fp and str(sf or "") == ctx.schema_fp

    # 无指纹：历史人工 QA，可配置是否保留
    return include_legacy


def upsert_nl2sql_auto_qa_pair(
    rag: RAGService,
    *,
    question: str,
    sql: str,
    data_source_fp: str,
    schema_fp: str,
    policy_fp: str,
    analysis_type: str | None,
    plan_item_id: str | None,
    prompt_prefix_snapshot: str | None,
) -> str:
    """
    幂等写入：先按 doc_name + doc_version 删除再索引单 chunk。
    返回 doc_name。
    """
    text = format_nl2sql_qa_embedding_text(
        question=question,
        sql=sql,
        prompt_prefix_snapshot=prompt_prefix_snapshot,
    )
    doc_name = build_nl2sql_auto_qa_doc_name(
        data_source_fp=data_source_fp,
        schema_fp=schema_fp,
        policy_fp=policy_fp,
        analysis_type=analysis_type,
        plan_item_id=plan_item_id,
        question=question,
    )
    meta = {
        "doc_version": NL2SQL_QA_DOC_VERSION_AUTO,
        META_KEY_INGEST_SOURCE: INGEST_SOURCE_AUTO,
        META_KEY_AUTO_KIND: NL2SQL_QA_AUTO_KIND,
        META_KEY_DATA_SOURCE_FP: data_source_fp,
        META_KEY_SCHEMA_FP: schema_fp,
        META_KEY_POLICY_FP: policy_fp,
        "analysis_type": (analysis_type or "").strip(),
        "plan_item_id": (plan_item_id or "").strip(),
        "question_normalized": normalize_nl2sql_question(question),
    }
    deleted = rag.delete_by_doc_name(
        doc_name,
        namespace=NL2SQLRAGService.NS_QA,
        doc_version=NL2SQL_QA_DOC_VERSION_AUTO,
    )
    if deleted:
        logger.debug("NL2SQL QA feedback replaced doc_name=%s deleted=%d", doc_name, deleted)
    rag.index_texts(
        [text],
        namespace=NL2SQLRAGService.NS_QA,
        doc_name=doc_name,
        ids=[doc_name],
        metadatas=[meta],
    )
    logger.info(
        "NL2SQL QA feedback indexed doc_name=%s ns=%s",
        doc_name,
        NL2SQLRAGService.NS_QA,
    )
    return doc_name


def list_nl2sql_auto_qa_entries(
    rag: RAGService,
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    列出系统自动写入的 QA（尽力而为：Faiss 内存库全表扫描；其它后端走 metadata_search）。
    """
    store = rag._store_provider.get_default_store()  # noqa: SLF001
    items_attr = getattr(store, "_items", None)
    out: list[dict[str, Any]] = []
    if isinstance(items_attr, dict):
        for _iid, item in items_attr.items():
            if item.get("namespace") != NL2SQLRAGService.NS_QA:
                continue
            meta = item.get("metadata") or {}
            if meta.get(META_KEY_AUTO_KIND) != NL2SQL_QA_AUTO_KIND:
                continue
            if meta.get(META_KEY_INGEST_SOURCE) != INGEST_SOURCE_AUTO:
                continue
            out.append(
                {
                    "doc_name": item.get("doc_name"),
                    "ext_id": item.get("ext_id"),
                    "namespace": item.get("namespace"),
                    "text": item.get("text"),
                    "metadata": meta,
                }
            )
            if len(out) >= limit:
                break
        return out

    hits = store.metadata_search(NL2SQL_QA_AUTO_KIND, k=min(limit, 2000), namespace=NL2SQLRAGService.NS_QA)
    for h in hits:
        meta = h.get("metadata") or {}
        if meta.get(META_KEY_AUTO_KIND) != NL2SQL_QA_AUTO_KIND:
            continue
        out.append(
            {
                "doc_name": h.get("doc_name"),
                "ext_id": h.get("ext_id"),
                "namespace": h.get("namespace"),
                "text": h.get("text"),
                "metadata": meta,
            }
        )
        if len(out) >= limit:
            break
    return out


def update_nl2sql_auto_qa_entry(
    rag: RAGService,
    *,
    doc_name: str,
    question: str,
    sql: str,
    prompt_prefix_snapshot: str | None = None,
    metadata_patch: dict[str, Any] | None = None,
) -> None:
    """管理端更新：删后重写；metadata 在自动写入字段基础上合并 patch。"""
    entries = list_nl2sql_auto_qa_entries(rag, limit=5000)
    base_meta: dict[str, Any] | None = None
    for e in entries:
        if e.get("doc_name") == doc_name:
            base_meta = dict(e.get("metadata") or {})
            break
    if base_meta is None:
        raise ValueError(f"doc_name not found in nl2sql auto QA: {doc_name!r}")
    text = format_nl2sql_qa_embedding_text(
        question=question,
        sql=sql,
        prompt_prefix_snapshot=prompt_prefix_snapshot,
    )
    merged = {**base_meta, **(metadata_patch or {})}
    rag.delete_by_doc_name(doc_name, namespace=NL2SQLRAGService.NS_QA, doc_version=NL2SQL_QA_DOC_VERSION_AUTO)
    rag.index_texts(
        [text],
        namespace=NL2SQLRAGService.NS_QA,
        doc_name=doc_name,
        ids=[doc_name],
        metadatas=[merged],
    )
