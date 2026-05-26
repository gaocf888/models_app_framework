"""
NL2SQL 向量库问答闭环：自动写入校验通过的 Q→SQL，并在检索侧按数据源/schema 指纹过滤。

见 AnalysisConfig.nl2sql_qa_feedback_enabled / NL2SQL_QA_FILTER_ENABLED。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any, Literal

from app.nl2sql.schema_service import SchemaMetadataService

from app.core.logging import get_logger
from app.nl2sql.rag_service import NL2SQLRAGService
from app.nl2sql.sql_cache import normalize_nl2sql_question, strip_plan_context_guide_suffix
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
META_KEY_DEDUP_NAMESPACE = "dedup_namespace"
META_KEY_DEDUP_INGEST_SOURCE = "dedup_ingest_source"
META_KEY_PLAN_TEMPLATE_VERSION = "plan_template_version"

# 须与 app.llm.graphs.analysis_graph_runner._PLAN_TASK_SCOPE_GUARD_CN 完全一致
_NL2SQL_PLAN_TASK_SCOPE_GUARD_CN = "若用户未指定机组/区域，则不要在 WHERE 中臆造具体锅炉名或墙别。"


def compact_nl2sql_feedback_question(question: str) -> str:
    """
    写入 QA / doc_name 前压缩问句：
    1) 去掉 plan_context 注入的「请结合以下规则线索…」及之后全文；
    2) 去掉综合分析子任务统一的机组/区域 scope 守卫（与运行时附加文案一致），仅保留「用户原句 + 计划模板子句」主干。
    不影响指纹过滤（metadata）；语义召回对齐「用户意图 + 子任务描述」。
    """
    s = strip_plan_context_guide_suffix(question)
    if not s:
        return s
    if os.getenv("NL2SQL_QA_FEEDBACK_KEEP_SCOPE_GUARD", "false").lower() != "true":
        g = _NL2SQL_PLAN_TASK_SCOPE_GUARD_CN
        if s.endswith(g):
            s = s[: -len(g)].rstrip()
    return s


@dataclass(frozen=True)
class NL2SQLQARetrievalContext:
    """当前请求上下文：仅保留与自动写入时一致的 QA 片段。"""

    data_source_fp: str
    schema_fp: str
    policy_fp: str
    analysis_type: str | None = None
    plan_item_id: str | None = None
    plan_template_version: str | None = None


def nl2sql_qa_slot_lookup_enabled() -> bool:
    """综合分析 acquire_data 子任务按五元组直取 QA 的总开关（默认开启）。"""
    return os.getenv("NL2SQL_QA_SLOT_LOOKUP_ENABLED", "true").lower() == "true"


def nl2sql_qa_slot_lookup_eligible(ctx: NL2SQLQARetrievalContext | None) -> bool:
    """
    是否对 nl2sql_qa_examples 走五元组 doc_name 精确取 1 条（非向量 Top-K）。
    需同时具备 analysis_type + plan_item_id + plan_template_version（且版本非 unknown）。
    """
    if ctx is None or not nl2sql_qa_slot_lookup_enabled():
        return False
    if not analysis_accepts_auto_qa_feedback(ctx.analysis_type, ctx.plan_item_id):
        return False
    ptv = normalize_plan_template_version(ctx.plan_template_version)
    return bool(ptv and ptv != "unknown")


def normalize_plan_template_version(plan_template_version: str | None) -> str:
    """写入/去重用的 plan 模板版本标签；空则 unknown（兼容未传参的旧调用）。"""
    s = (plan_template_version or "").strip()
    return s if s else "unknown"


def build_nl2sql_auto_qa_dedup_key(
    *,
    namespace: str,
    ingest_source: str,
    analysis_type: str,
    plan_item_id: str,
    plan_template_version: str,
) -> str:
    """五元组去重键（明文，供 metadata 与运维排查）。"""
    ptv = normalize_plan_template_version(plan_template_version)
    return (
        f"{(namespace or '').strip()}\0{(ingest_source or '').strip()}\0"
        f"{(analysis_type or '').strip()}\0{(plan_item_id or '').strip()}\0{ptv}"
    )


def build_nl2sql_auto_qa_doc_name(
    *,
    namespace: str = NL2SQLRAGService.NS_QA,
    ingest_source: str = INGEST_SOURCE_AUTO,
    analysis_type: str,
    plan_item_id: str,
    plan_template_version: str,
) -> str:
    """由五元组 (namespace, ingest_source, analysis_type, plan_item_id, plan_template_version) 确定性生成 doc_name。"""
    raw = build_nl2sql_auto_qa_dedup_key(
        namespace=namespace,
        ingest_source=ingest_source,
        analysis_type=analysis_type,
        plan_item_id=plan_item_id,
        plan_template_version=plan_template_version,
    )
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"nl2sql_auto_{h}"


def analysis_accepts_auto_qa_feedback(
    analysis_type: str | None,
    plan_item_id: str | None,
) -> bool:
    """
    仅综合分析数据计划子任务（analysis_type + plan_item_id 均非空）允许自动写入。
    直连 POST /nl2sql/query 未传 plan_item_id 或 analysis_type 为空时不写入。
    """
    return bool((analysis_type or "").strip() and (plan_item_id or "").strip())


def nl2sql_auto_qa_doc_exists(
    rag: RAGService,
    *,
    doc_name: str,
    namespace: str = NL2SQLRAGService.NS_QA,
    doc_version: str = NL2SQL_QA_DOC_VERSION_AUTO,
) -> bool:
    """向量库中是否已有该 doc_name（同 namespace + doc_version）。"""
    store = rag._store_provider.get_default_store()  # noqa: SLF001
    list_fn = getattr(store, "list_chunk_texts_for_document", None)
    if callable(list_fn):
        return bool(
            list_fn(
                doc_name,
                namespace=namespace,
                doc_version=doc_version,
            )
        )
    for entry in list_nl2sql_auto_qa_entries(rag, limit=5000):
        if entry.get("doc_name") == doc_name:
            return True
    return False


def format_nl2sql_qa_embedding_text(
    *,
    question: str,
    sql: str,
    prompt_prefix_snapshot: str | None,
    max_prefix_chars: int | None = None,
) -> str:
    """
    写入向量库的单一文本：最小可用集合，优先「紧凑问句 + SQL」语义对齐；
    预制提示前缀默认不写（易含整库表清单，体积巨大且与 schema_fp 元数据重复）。
    max_prefix_chars：None 时读取环境变量 NL2SQL_QA_EMBED_PREFIX_MAX_CHARS（默认 0）。
    """
    q_compact = normalize_nl2sql_question(compact_nl2sql_feedback_question(question))
    lim = max_prefix_chars
    if lim is None:
        lim = int(os.getenv("NL2SQL_QA_EMBED_PREFIX_MAX_CHARS", "0"))
    lim = max(0, int(lim))
    prefix = (prompt_prefix_snapshot or "").strip()
    if lim == 0:
        prefix = ""
    elif len(prefix) > lim:
        prefix = prefix[: max(lim - 3, 0)] + ("..." if lim > 3 else "")
    sql_lim_env = int(os.getenv("NL2SQL_QA_EMBED_SQL_MAX_CHARS", "16000"))
    sql_lim = max(256, sql_lim_env)
    sql_body = (sql or "").strip()
    if len(sql_body) > sql_lim:
        sql_body = sql_body[: sql_lim - 3] + "..."
    parts = [
        "【NL2SQL 问答样例·系统自动写入】",
        f"【用户问题】{q_compact}",
    ]
    if prefix:
        parts.append(f"【预制提示前缀摘要】\n{prefix}")
    parts.append(f"【校验通过的 SQL】\n{sql_body}")
    return "\n\n".join(parts)


def _load_nl2sql_auto_qa_entries_by_doc_name(
    rag: RAGService,
    doc_name: str,
    *,
    namespace: str = NL2SQLRAGService.NS_QA,
    doc_version: str = NL2SQL_QA_DOC_VERSION_AUTO,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """
    按 doc_name 直取向量库 chunk（含 metadata），不受 metadata_search scan_cap 限制。
    """
    store = rag._store_provider.get_default_store()  # noqa: SLF001
    list_fn = getattr(store, "list_chunks_for_document", None)
    if not callable(list_fn):
        return []
    rows = list_fn(
        doc_name,
        namespace=namespace,
        doc_version=doc_version,
        limit=max(1, limit),
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "")
        if not text:
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        out.append(
            {
                "doc_name": doc_name,
                "ext_id": row.get("ext_id"),
                "namespace": row.get("namespace") or namespace,
                "text": text,
                "metadata": meta,
            }
        )
    return out


def fetch_nl2sql_qa_chunks_by_slot(
    rag: RAGService,
    ctx: NL2SQLQARetrievalContext,
    *,
    max_chunks: int = 1,
) -> list[RetrievedChunk]:
    """
    按五元组 (namespace, ingest_source, analysis_type, plan_item_id, plan_template_version)
    精确加载 nl2sql_qa_examples 中唯一 doc；再经指纹过滤。
    未命中或指纹不匹配时返回空列表（由调用方回退向量检索）。
    """
    if not nl2sql_qa_slot_lookup_eligible(ctx):
        return []

    at = (ctx.analysis_type or "").strip()
    pid = (ctx.plan_item_id or "").strip()
    ptv = normalize_plan_template_version(ctx.plan_template_version)
    doc_name = build_nl2sql_auto_qa_doc_name(
        analysis_type=at,
        plan_item_id=pid,
        plan_template_version=ptv,
    )

    entries = _load_nl2sql_auto_qa_entries_by_doc_name(
        rag,
        doc_name,
        namespace=NL2SQLRAGService.NS_QA,
        doc_version=NL2SQL_QA_DOC_VERSION_AUTO,
        limit=max(1, max_chunks),
    )
    if not entries:
        # 兼容未实现 list_chunks_for_document 的旧 store
        entries = [
            e
            for e in list_nl2sql_auto_qa_entries(
                rag,
                limit=max(1, max_chunks),
                analysis_type=at,
                plan_item_id=pid,
                plan_template_version=ptv,
            )
            if str(e.get("doc_name") or "") == doc_name
        ]

    out: list[RetrievedChunk] = []
    for entry in entries:
        meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        chunk = RetrievedChunk(
            text=str(entry.get("text") or ""),
            doc_name=doc_name,
            namespace=str(entry.get("namespace") or NL2SQLRAGService.NS_QA),
            chunk_id=str(entry.get("ext_id") or doc_name),
            score=1.0,
            metadata=meta,
        )
        if not (chunk.text or "").strip():
            continue
        if qa_chunk_passes_retrieval_filter(chunk, ctx):
            out.append(chunk)
        if len(out) >= max(1, max_chunks):
            break

    logger.info(
        "NL2SQL QA slot lookup doc_name=%s analysis_type=%s plan_item_id=%s plan_template_version=%s hit=%s",
        doc_name,
        at,
        pid,
        ptv,
        bool(out),
    )
    return out


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
        ptv_ctx = normalize_plan_template_version(ctx.plan_template_version) if ctx.plan_template_version else None
        if ptv_ctx and ptv_ctx != "unknown":
            ptv_m = meta.get(META_KEY_PLAN_TEMPLATE_VERSION)
            if ptv_m not in (None, ""):
                if normalize_plan_template_version(str(ptv_m)) != ptv_ctx:
                    return False
            elif os.getenv("NL2SQL_QA_INCLUDE_LEGACY_NO_PLAN_VER", "true").lower() != "true":
                return False
        return True

    # 显式打了指纹的人工/旧版文档：按指纹约束
    ds = meta.get(META_KEY_DATA_SOURCE_FP)
    sf = meta.get(META_KEY_SCHEMA_FP)
    if ds or sf:
        return str(ds or "") == ctx.data_source_fp and str(sf or "") == ctx.schema_fp

    # 无指纹：历史人工 QA，可配置是否保留
    return include_legacy


Nl2sqlAutoQaWriteMode = Literal["skip_if_exists", "replace"]


@dataclass(frozen=True)
class ResolvedNl2sqlQaFingerprints:
    data_source_fp: str
    schema_fp: str
    policy_fp: str


def resolve_nl2sql_qa_fingerprints(
    *,
    data_source_fp: str | None = None,
    schema_fp: str | None = None,
    policy_fp: str | None = None,
    analysis_type: str | None = None,
) -> ResolvedNl2sqlQaFingerprints:
    """
    解析自动 QA 写入所需指纹：显式传入优先，否则用当前应用 DB 配置与内存 Schema 目录。
    """
    from app.core.config import get_app_config
    from app.nl2sql.sql_cache import (
        compute_nl2sql_data_source_fp,
        compute_nl2sql_policy_fp,
        compute_schema_fp_from_metadata,
    )

    app_cfg = get_app_config()
    db_cfg = getattr(app_cfg, "db", None)

    ds = (data_source_fp or "").strip()
    if not ds:
        if db_cfg is None:
            raise ValueError("data_source_fp is required when application DB config is not available")
        ds = compute_nl2sql_data_source_fp(
            host=db_cfg.host,
            port=db_cfg.port,
            database=db_cfg.database,
        )

    sc = (schema_fp or "").strip()
    if not sc:
        table_names = [t.name for t in SchemaMetadataService().list_tables() if t.name]
        if not table_names:
            raise ValueError(
                "schema_fp is required when schema catalog is empty; "
                "pass schema_fp explicitly or refresh NL2SQL schema from DB first"
            )
        sc = compute_schema_fp_from_metadata(table_names)

    pol = (policy_fp or "").strip()
    if not pol:
        pol = compute_nl2sql_policy_fp(analysis_type=analysis_type)

    return ResolvedNl2sqlQaFingerprints(data_source_fp=ds, schema_fp=sc, policy_fp=pol)


def _nl2sql_auto_qa_slot_ids(
    *,
    analysis_type: str | None,
    plan_item_id: str | None,
    plan_template_version: str | None,
) -> tuple[str, str, str, str, str]:
    if not analysis_accepts_auto_qa_feedback(analysis_type, plan_item_id):
        raise ValueError("analysis_type and plan_item_id are both required for nl2sql auto QA")
    at = (analysis_type or "").strip()
    pid = (plan_item_id or "").strip()
    ptv = normalize_plan_template_version(plan_template_version)
    ns = NL2SQLRAGService.NS_QA
    src = INGEST_SOURCE_AUTO
    doc_name = build_nl2sql_auto_qa_doc_name(
        namespace=ns,
        ingest_source=src,
        analysis_type=at,
        plan_item_id=pid,
        plan_template_version=ptv,
    )
    dedup_key = build_nl2sql_auto_qa_dedup_key(
        namespace=ns,
        ingest_source=src,
        analysis_type=at,
        plan_item_id=pid,
        plan_template_version=ptv,
    )
    return at, pid, ptv, doc_name, dedup_key


def _index_nl2sql_auto_qa_pair(
    rag: RAGService,
    *,
    doc_name: str,
    question: str,
    sql: str,
    data_source_fp: str,
    schema_fp: str,
    policy_fp: str,
    analysis_type: str,
    plan_item_id: str,
    plan_template_version: str,
    prompt_prefix_snapshot: str | None,
    metadata_extra: dict[str, Any] | None = None,
) -> None:
    ns = NL2SQLRAGService.NS_QA
    src = INGEST_SOURCE_AUTO
    text = format_nl2sql_qa_embedding_text(
        question=question,
        sql=sql,
        prompt_prefix_snapshot=prompt_prefix_snapshot,
    )
    meta = {
        "doc_version": NL2SQL_QA_DOC_VERSION_AUTO,
        META_KEY_INGEST_SOURCE: src,
        META_KEY_AUTO_KIND: NL2SQL_QA_AUTO_KIND,
        META_KEY_DEDUP_NAMESPACE: ns,
        META_KEY_DEDUP_INGEST_SOURCE: src,
        META_KEY_DATA_SOURCE_FP: data_source_fp,
        META_KEY_SCHEMA_FP: schema_fp,
        META_KEY_POLICY_FP: policy_fp,
        "analysis_type": analysis_type,
        "plan_item_id": plan_item_id,
        META_KEY_PLAN_TEMPLATE_VERSION: plan_template_version,
        "dedup_key": build_nl2sql_auto_qa_dedup_key(
            namespace=ns,
            ingest_source=src,
            analysis_type=analysis_type,
            plan_item_id=plan_item_id,
            plan_template_version=plan_template_version,
        ),
        "question_normalized": normalize_nl2sql_question(compact_nl2sql_feedback_question(question)),
    }
    if metadata_extra:
        meta.update(metadata_extra)
    rag.index_texts(
        [text],
        namespace=ns,
        doc_name=doc_name,
        ids=[doc_name],
        metadatas=[meta],
    )


def create_nl2sql_auto_qa_entry(
    rag: RAGService,
    *,
    question: str,
    sql: str,
    data_source_fp: str,
    schema_fp: str,
    policy_fp: str,
    analysis_type: str,
    plan_item_id: str,
    plan_template_version: str | None,
    prompt_prefix_snapshot: str | None = None,
    mode: Nl2sqlAutoQaWriteMode = "replace",
) -> tuple[str, bool, str]:
    """
    管理端半自动写入：按五元组定位 doc_name。
    - skip_if_exists：已存在则跳过（与运行时自动写入一致）
    - replace：已存在则删后写（等同未知 doc_name 的 PATCH）
    返回 (doc_name, created, dedup_key)；created=False 表示未写入（仅 skip_if_exists 跳过）。
    """
    if mode not in ("skip_if_exists", "replace"):
        raise ValueError(f"invalid mode: {mode!r}")

    at, pid, ptv, doc_name, dedup_key = _nl2sql_auto_qa_slot_ids(
        analysis_type=analysis_type,
        plan_item_id=plan_item_id,
        plan_template_version=plan_template_version,
    )
    ns = NL2SQLRAGService.NS_QA
    exists = nl2sql_auto_qa_doc_exists(rag, doc_name=doc_name, namespace=ns)

    if exists and mode == "skip_if_exists":
        logger.info(
            "NL2SQL QA admin create skipped existing doc_name=%s dedup=%s",
            doc_name,
            dedup_key,
        )
        return doc_name, False, dedup_key

    if exists and mode == "replace":
        rag.delete_by_doc_name(doc_name, namespace=ns, doc_version=NL2SQL_QA_DOC_VERSION_AUTO)

    _index_nl2sql_auto_qa_pair(
        rag,
        doc_name=doc_name,
        question=question,
        sql=sql,
        data_source_fp=data_source_fp,
        schema_fp=schema_fp,
        policy_fp=policy_fp,
        analysis_type=at,
        plan_item_id=pid,
        plan_template_version=ptv,
        prompt_prefix_snapshot=prompt_prefix_snapshot,
    )
    logger.info(
        "NL2SQL QA admin create indexed doc_name=%s mode=%s analysis_type=%s plan_item_id=%s plan_template_version=%s",
        doc_name,
        mode,
        at,
        pid,
        ptv,
    )
    return doc_name, True, dedup_key


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
    plan_template_version: str | None,
    prompt_prefix_snapshot: str | None,
) -> str | None:
    """
    按五元组 (namespace, ingest_source, analysis_type, plan_item_id, plan_template_version) 仅首次写入。
    已存在则跳过（不覆盖）；不满足综合分析子任务条件时不写入。
    返回 doc_name；跳过或拒绝时返回 None。
    """
    if not analysis_accepts_auto_qa_feedback(analysis_type, plan_item_id):
        logger.debug(
            "NL2SQL QA feedback skipped: missing analysis_type or plan_item_id "
            "analysis_type=%r plan_item_id=%r",
            analysis_type,
            plan_item_id,
        )
        return None

    try:
        doc_name, created, _dedup = create_nl2sql_auto_qa_entry(
            rag,
            question=question,
            sql=sql,
            data_source_fp=data_source_fp,
            schema_fp=schema_fp,
            policy_fp=policy_fp,
            analysis_type=(analysis_type or "").strip(),
            plan_item_id=(plan_item_id or "").strip(),
            plan_template_version=plan_template_version,
            prompt_prefix_snapshot=prompt_prefix_snapshot,
            mode="skip_if_exists",
        )
    except ValueError:
        return None
    return doc_name if created else None


def _nl2sql_auto_qa_entry_matches_filters(
    meta: dict[str, Any],
    *,
    analysis_type: str | None,
    plan_item_id: str | None,
    plan_template_version: str | None,
) -> bool:
    if analysis_type is not None and str(meta.get("analysis_type") or "").strip() != analysis_type.strip():
        return False
    if plan_item_id is not None and str(meta.get("plan_item_id") or "").strip() != plan_item_id.strip():
        return False
    if plan_template_version is not None:
        want = normalize_plan_template_version(plan_template_version)
        got = normalize_plan_template_version(
            str(meta.get(META_KEY_PLAN_TEMPLATE_VERSION) or meta.get("plan_template_version") or "")
        )
        if got != want:
            return False
    return True


def list_nl2sql_auto_qa_entries(
    rag: RAGService,
    *,
    limit: int = 500,
    analysis_type: str | None = None,
    plan_item_id: str | None = None,
    plan_template_version: str | None = None,
) -> list[dict[str, Any]]:
    """
    列出系统自动写入的 QA（尽力而为：Faiss 内存库全表扫描；其它后端走 metadata_search）。
    可选按 analysis_type / plan_item_id / plan_template_version 过滤（精确匹配，plan 版本经 normalize）。
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
            if not _nl2sql_auto_qa_entry_matches_filters(
                meta,
                analysis_type=analysis_type,
                plan_item_id=plan_item_id,
                plan_template_version=plan_template_version,
            ):
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

    scan_cap = min(5000, max(limit * 20, limit))
    hits = store.metadata_search(NL2SQL_QA_AUTO_KIND, k=scan_cap, namespace=NL2SQLRAGService.NS_QA)
    for h in hits:
        meta = h.get("metadata") or {}
        if meta.get(META_KEY_AUTO_KIND) != NL2SQL_QA_AUTO_KIND:
            continue
        if not _nl2sql_auto_qa_entry_matches_filters(
            meta,
            analysis_type=analysis_type,
            plan_item_id=plan_item_id,
            plan_template_version=plan_template_version,
        ):
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
    merged = {**base_meta, **(metadata_patch or {})}
    at = str(merged.get("analysis_type") or "").strip()
    pid = str(merged.get("plan_item_id") or "").strip()
    if at and pid:
        ns = str(merged.get(META_KEY_DEDUP_NAMESPACE) or NL2SQLRAGService.NS_QA).strip()
        src = str(merged.get(META_KEY_DEDUP_INGEST_SOURCE) or INGEST_SOURCE_AUTO).strip()
        ptv = normalize_plan_template_version(
            str(merged.get(META_KEY_PLAN_TEMPLATE_VERSION) or merged.get("plan_template_version") or "")
        )
        merged[META_KEY_PLAN_TEMPLATE_VERSION] = ptv
        merged["dedup_key"] = build_nl2sql_auto_qa_dedup_key(
            namespace=ns,
            ingest_source=src,
            analysis_type=at,
            plan_item_id=pid,
            plan_template_version=ptv,
        )
    rag.delete_by_doc_name(doc_name, namespace=NL2SQLRAGService.NS_QA, doc_version=NL2SQL_QA_DOC_VERSION_AUTO)
    ptv = normalize_plan_template_version(
        str(merged.get(META_KEY_PLAN_TEMPLATE_VERSION) or merged.get("plan_template_version") or "")
    )
    metadata_extra = dict(metadata_patch) if metadata_patch else None
    _index_nl2sql_auto_qa_pair(
        rag,
        doc_name=doc_name,
        question=question,
        sql=sql,
        data_source_fp=str(merged.get(META_KEY_DATA_SOURCE_FP) or ""),
        schema_fp=str(merged.get(META_KEY_SCHEMA_FP) or ""),
        policy_fp=str(merged.get(META_KEY_POLICY_FP) or ""),
        analysis_type=at or str(merged.get("analysis_type") or ""),
        plan_item_id=pid or str(merged.get("plan_item_id") or ""),
        plan_template_version=ptv,
        prompt_prefix_snapshot=prompt_prefix_snapshot,
        metadata_extra=metadata_extra or None,
    )
