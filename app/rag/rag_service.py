from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Sequence, Set

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import (
    RAG_DOC_DELETE_COUNT,
    RAG_KEYWORD_RECALL_COUNT,
    RAG_METADATA_RECALL_COUNT,
    RAG_QUERY_COUNT,
    RAG_RERANK_COUNT,
    RAG_SEMANTIC_RECALL_COUNT,
)
from app.rag.embedding_service import (
    EmbeddingService,
    RAG_MODEL_TORCH_DTYPE,
    _is_qwen_reranker_model,
    rag_cross_encoder_load_kwargs,
)
from app.rag.text_quality import looks_like_binary_text
from app.rag.models import RetrievedChunk
from app.rag.namespace_kb import finalize_retrieval_hits
from app.rag.service_registry import get_embedding_service, get_vector_store_provider
from app.rag.vector_store import VectorStoreProvider

logger = get_logger(__name__)


def _normalize_excluded_namespaces(exclude_namespaces: Sequence[str] | None) -> Set[str] | None:
    if not exclude_namespaces:
        return None
    out = {str(n).strip() for n in exclude_namespaces if n is not None and str(n).strip()}
    return out or None


def _hit_namespace_allowed(hit: dict, excluded: Set[str]) -> bool:
    ns = str(hit.get("namespace") or "").strip()
    return ns not in excluded


def _hit_base_score(hit: dict) -> float:
    if hit.get("_priority_adjusted_score") is not None:
        return float(hit["_priority_adjusted_score"])
    if hit.get("_rerank_score") is not None:
        return float(hit["_rerank_score"])
    if hit.get("_fused_score") is not None:
        return float(hit["_fused_score"])
    if hit.get("score") is not None:
        return float(hit["score"])
    return 0.0


def _finalize_retrieval_hits(
    hits: list[dict],
    *,
    namespace: str | None,
    priority_boost: float,
    priority_tiered: bool,
    k_out: int,
) -> list[dict]:
    return finalize_retrieval_hits(
        hits,
        namespace=namespace,
        priority_boost=priority_boost,
        priority_tiered=priority_tiered,
        k_out=k_out,
        score_getter=_hit_base_score,
    )


def _cross_encoder_device_repr(reranker: object) -> str:
    """CrossEncoder 所用设备：新版为 `device`，旧版曾为 `_target_device`（访问后者会触发弃用告警）。"""
    try:
        return str(reranker.device)  # type: ignore[attr-defined]
    except AttributeError:
        return str(getattr(reranker, "_target_device", "?"))


class RAGService:
    """
    统一的 RAG 检索服务。

    当前版本：
    - 使用 EmbeddingService 生成嵌入；
    - 使用 VectorStoreProvider 提供的存储后端执行语义检索与关键词检索；
    - 默认启用“语义召回 + 关键词召回 + RRF 融合 + CrossEncoder 重排”；
    - 支持按业务场景读取差异化检索参数（top_k/召回规模/重排规模）。
    """

    def __init__(self, embedding_service: EmbeddingService | None = None, store_provider: VectorStoreProvider | None = None) -> None:
        self._cfg = get_app_config().rag
        self._embedding_service = embedding_service or get_embedding_service()
        self._store_provider = store_provider or get_vector_store_provider()
        self._reranker = None
        self._reranker_lock = threading.Lock()

    def _get_reranker(self):
        if self._reranker is not None:
            return self._reranker
        with self._reranker_lock:
            if self._reranker is not None:
                return self._reranker
            backend = (getattr(self._cfg.hybrid, "reranker_backend", None) or "mis_tei").strip().lower()
            if backend == "mis_tei":
                try:
                    from app.rag.mis_tei_client import MisTeiReranker

                    tei = get_app_config().mis_tei
                    self._reranker = MisTeiReranker(
                        base_url=tei.rerank_base_url,
                        timeout_s=tei.timeout_s,
                        batch_size=tei.rerank_batch_size,
                    )
                    logger.info(
                        "RAGService loaded MisTeiReranker base_url=%s batch_size=%s",
                        tei.rerank_base_url,
                        tei.rerank_batch_size,
                    )
                    return self._reranker
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "RAGService failed to init MisTeiReranker; skip rerank. err=%s",
                        e,
                    )
                    self._reranker = None
                    return None

            hub_name = (self._cfg.hybrid.reranker_model_name or "BAAI/bge-reranker-large").strip()
            raw_path = (self._cfg.hybrid.reranker_model_path or "").strip()
            configured_device = (self._cfg.hybrid.reranker_device or "").strip() or None
            resolved_local: str | None = None
            if raw_path:
                expanded = os.path.abspath(os.path.expandvars(os.path.expanduser(raw_path)))
                if os.path.isdir(expanded):
                    resolved_local = expanded
                else:
                    # 路径无效时勿把绝对路径当作 HF repo id 传给 CrossEncoder（会触发 Repo id must be...）
                    logger.warning(
                        "RAG_RERANKER_MODEL_PATH is not a directory (%s); falling back to hub id %s",
                        expanded,
                        hub_name,
                    )
            load_id = resolved_local if resolved_local else hub_name
            trust_remote_code = os.getenv("RAG_RERANKER_TRUST_REMOTE_CODE", "false").lower() == "true"
            use_qwen_native = _is_qwen_reranker_model(load_id) or (
                trust_remote_code and _is_qwen_reranker_model(hub_name)
            )
            try:
                if use_qwen_native:
                    from app.rag.qwen3_reranker import Qwen3Reranker

                    max_length = int(os.getenv("RAG_RERANKER_MAX_LENGTH", "8192"))
                    self._reranker = Qwen3Reranker(
                        load_id,
                        device=configured_device,
                        max_length=max_length,
                    )
                    logger.info(
                        "RAGService loaded Qwen3Reranker (native): %s device=%s configured_device=%s",
                        load_id,
                        self._reranker.device,
                        configured_device or "auto",
                    )
                    return self._reranker

                from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]

                common_kwargs: dict[str, Any] = rag_cross_encoder_load_kwargs(
                    trust_remote_code=trust_remote_code,
                    model_id=load_id,
                )
                if configured_device:
                    common_kwargs["device"] = configured_device
                if resolved_local:
                    self._reranker = CrossEncoder(
                        resolved_local,
                        **common_kwargs,
                    )
                else:
                    self._reranker = CrossEncoder(
                        hub_name,
                        **common_kwargs,
                    )
                target_device = _cross_encoder_device_repr(self._reranker)
                logger.info(
                    "RAGService loaded CrossEncoder reranker: %s device=%s configured_device=%s torch_dtype=%s",
                    load_id,
                    target_device,
                    configured_device or "auto",
                    RAG_MODEL_TORCH_DTYPE,
                )
                return self._reranker
            except ImportError as e:
                raise ImportError(
                    "CrossEncoder reranker requires sentence-transformers. "
                    "Install with: pip install -r requirements-大模型应用.txt"
                ) from e
            except Exception as e:  # noqa: BLE001
                logger.warning("RAGService failed to load reranker model=%s; skip rerank. err=%s", load_id, e)
                self._reranker = None
                return None

    def index_texts(
        self,
        texts: Sequence[str],
        namespace: str | None = None,
        doc_name: str | None = None,
        ids: Sequence[str] | None = None,
        metadatas: Sequence[dict | None] | None = None,
    ) -> None:
        """
        将一批文本加入默认向量库。
        说明：真正生产环境中，这通常在离线摄入流程（RAGIngestionService）中调用。
        """
        embs = self._embedding_service.embed_texts(list(texts))
        store = self._store_provider.get_default_store()
        store.add_texts(
            texts,
            embeddings=embs,
            ids=ids,
            namespace=namespace,
            doc_name=doc_name,
            metadatas=metadatas,
        )

    @staticmethod
    def _hit_to_chunk(hit: dict, pipeline_version: str | None) -> RetrievedChunk:
        meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
        rerank_score = None
        if hit.get("_rerank_score") is not None:
            rerank_score = float(hit["_rerank_score"])
        score = None
        if hit.get("_priority_adjusted_score") is not None:
            score = float(hit["_priority_adjusted_score"])
        elif rerank_score is not None:
            score = rerank_score
        elif hit.get("_fused_score") is not None:
            score = float(hit["_fused_score"])
        elif hit.get("score") is not None:
            score = float(hit["score"])
        section = meta.get("section_path") or meta.get("section")
        dver = meta.get("doc_version")
        return RetrievedChunk(
            text=str(hit.get("text", "")),
            doc_name=hit.get("doc_name"),
            namespace=hit.get("namespace"),
            chunk_id=str(hit.get("ext_id")) if hit.get("ext_id") is not None else None,
            score=score,
            rerank_score=rerank_score,
            section_path=str(section) if section is not None else None,
            doc_version=str(dver) if dver is not None else None,
            pipeline_version=pipeline_version,
            metadata=meta,
        )

    def retrieve_chunks(
        self,
        query: str,
        top_k: int | None = None,
        namespace: str | None = None,
        use_hybrid: bool | None = None,
        scene: str | None = None,
        rerank_query: str | None = None,
        exclude_namespaces: Sequence[str] | None = None,
        query_image_url: str | None = None,
    ) -> List[RetrievedChunk]:
        """
        执行检索并返回标准 RetrievedChunk 列表（设计稿 §E 统一检索输出）。

        - query：用于向量嵌入与关键词/元数据召回（主检索句）。
        - query_image_url：可选用户附图 URL；需 ``RAG_QUERY_VISION_AUGMENT_ENABLED=true``。
        - rerank_query：若传入且在 hybrid 开启且 CrossEncoder 可用时，仅用于最后重排；
          用于「召回用用户原句、重排时再拼场景标签」等两阶段策略。
        - exclude_namespaces：检索结果中剔除这些 namespace（会放大内部召回规模再过滤截断）。
        """
        from app.rag.query_vision_augment import merge_retrieved_chunks, resolve_retrieval_query

        effective_query, hybrid_aug_query = resolve_retrieval_query(query, query_image_url)
        profile = self._get_scene_profile(scene)
        k_out = top_k or (profile.top_k if profile is not None else self._cfg.top_k)
        if hybrid_aug_query:
            primary = self._retrieve_chunks_core(
                query,
                top_k=k_out,
                namespace=namespace,
                use_hybrid=use_hybrid,
                scene=scene,
                rerank_query=rerank_query,
                exclude_namespaces=exclude_namespaces,
            )
            secondary = self._retrieve_chunks_core(
                hybrid_aug_query,
                top_k=k_out,
                namespace=namespace,
                use_hybrid=use_hybrid,
                scene=scene,
                rerank_query=rerank_query or hybrid_aug_query,
                exclude_namespaces=exclude_namespaces,
            )
            return merge_retrieved_chunks(primary, secondary, max_k=k_out)
        return self._retrieve_chunks_core(
            effective_query,
            top_k=top_k,
            namespace=namespace,
            use_hybrid=use_hybrid,
            scene=scene,
            rerank_query=rerank_query,
            exclude_namespaces=exclude_namespaces,
        )

    def _retrieve_chunks_core(
        self,
        query: str,
        top_k: int | None = None,
        namespace: str | None = None,
        use_hybrid: bool | None = None,
        scene: str | None = None,
        rerank_query: str | None = None,
        exclude_namespaces: Sequence[str] | None = None,
    ) -> List[RetrievedChunk]:
        RAG_QUERY_COUNT.inc()
        profile = self._get_scene_profile(scene)
        k_out = top_k or (profile.top_k if profile is not None else self._cfg.top_k)
        excluded = _normalize_excluded_namespaces(exclude_namespaces)
        recall_k = k_out
        if excluded or namespace is None:
            recall_k = min(max(k_out * 4, 32), 64)
        k = recall_k
        pv = self._cfg.ingestion.pipeline_version
        store = self._store_provider.get_default_store()
        q_emb = self._embedding_service.embed_text(query)
        hybrid_enabled = self._cfg.hybrid.enabled if use_hybrid is None else use_hybrid
        require_kb_enabled = True
        priority_boost = float(self._cfg.namespace_kb_priority_boost)
        priority_tiered = bool(self._cfg.namespace_kb_priority_tiered)
        hits: list[dict]
        if not hybrid_enabled:
            RAG_SEMANTIC_RECALL_COUNT.inc()
            hits = store.similarity_search_by_vector(
                q_emb, k=k, namespace=namespace, require_kb_enabled=require_kb_enabled
            )
            if excluded:
                hits = [h for h in hits if _hit_namespace_allowed(h, excluded)]
            hits = _finalize_retrieval_hits(
                hits,
                namespace=namespace,
                priority_boost=priority_boost,
                priority_tiered=priority_tiered,
                k_out=k_out,
            )
        else:
            sem_top = profile.semantic_top_k if profile is not None else self._cfg.hybrid.semantic_top_k
            kw_top = profile.keyword_top_k if profile is not None else self._cfg.hybrid.keyword_top_k
            md_top = self._cfg.hybrid.metadata_top_k
            sem_k = max(sem_top, k)
            kw_k = max(kw_top, k)
            md_k = max(md_top, k)
            metadata_enabled = bool(self._cfg.hybrid.metadata_recall_enabled)
            md_query = query
            if metadata_enabled and (scene or "").strip().lower() == "nl2sql":
                md_query = (
                    os.getenv("NL2SQL_HYBRID_METADATA_QUERY", "").strip()
                    or "nl2sql_system_feedback_v1"
                )
            worker_num = 3 if metadata_enabled else 2
            with ThreadPoolExecutor(max_workers=worker_num) as pool:
                f_sem = pool.submit(
                    store.similarity_search_by_vector,
                    q_emb,
                    sem_k,
                    namespace,
                    require_kb_enabled=require_kb_enabled,
                )
                f_kw = pool.submit(
                    store.keyword_search,
                    query,
                    kw_k,
                    namespace,
                    require_kb_enabled=require_kb_enabled,
                )
                f_md = (
                    pool.submit(
                        store.metadata_search,
                        md_query,
                        md_k,
                        namespace,
                        require_kb_enabled=require_kb_enabled,
                    )
                    if metadata_enabled
                    else None
                )
                semantic_hits = f_sem.result()
                keyword_hits = f_kw.result()
                metadata_hits = f_md.result() if f_md is not None else []
            if excluded:
                semantic_hits = [h for h in semantic_hits if _hit_namespace_allowed(h, excluded)]
                keyword_hits = [h for h in keyword_hits if _hit_namespace_allowed(h, excluded)]
                metadata_hits = [h for h in metadata_hits if _hit_namespace_allowed(h, excluded)]
            RAG_SEMANTIC_RECALL_COUNT.inc()
            RAG_KEYWORD_RECALL_COUNT.inc()
            if metadata_enabled:
                RAG_METADATA_RECALL_COUNT.inc()
            fused = self._rrf_fuse(
                semantic_hits=semantic_hits,
                keyword_hits=keyword_hits,
                metadata_hits=metadata_hits,
                rrf_k=self._cfg.hybrid.rrf_k,
            )
            rerank_base = profile.rerank_top_n if profile is not None else self._cfg.hybrid.rerank_top_n
            rerank_top_n = max(rerank_base, k)
            candidates = fused[:rerank_top_n]
            rr_q = rerank_query if (rerank_query is not None and str(rerank_query).strip()) else query
            reranked = self._rerank(query=rr_q, hits=candidates)
            hits = _finalize_retrieval_hits(
                reranked,
                namespace=namespace,
                priority_boost=priority_boost,
                priority_tiered=priority_tiered,
                k_out=k_out,
            )

        out: List[RetrievedChunk] = []
        for h in hits:
            if not h.get("text"):
                continue
            if looks_like_binary_text(str(h.get("text") or "")):
                logger.warning(
                    "RAGService skip binary-like chunk ext_id=%s doc_name=%s",
                    h.get("ext_id"),
                    h.get("doc_name"),
                )
                continue
            out.append(self._hit_to_chunk(h, pv))
            if len(out) >= k_out:
                break

        from app.rag.figure_retrieval_expand import expand_related_figures

        out = expand_related_figures(out, store, namespace=namespace, pipeline_version=pv)
        if len(out) > k_out:
            out = out[:k_out]
        return out

    def retrieve_context(
        self,
        query: str,
        top_k: int | None = None,
        namespace: str | None = None,
        use_hybrid: bool | None = None,
        scene: str | None = None,
        rerank_query: str | None = None,
        exclude_namespaces: Sequence[str] | None = None,
        query_image_url: str | None = None,
    ) -> List[str]:
        """
        执行检索并返回候选上下文文本列表。

        当 hybrid 启用时：
        - 并行执行语义召回与关键词召回；
        - 使用 RRF 融合候选；
        - 使用 CrossEncoder 重排并返回 Top-K。

        实现上委托 `retrieve_chunks`，保持与标准 RetrievedChunk 一致。
        """
        chunks = self.retrieve_chunks(
            query=query,
            top_k=top_k,
            namespace=namespace,
            use_hybrid=use_hybrid,
            scene=scene,
            rerank_query=rerank_query,
            exclude_namespaces=exclude_namespaces,
            query_image_url=query_image_url,
        )
        return [c.text for c in chunks if c.text]

    def delete_by_doc_name(self, doc_name: str, namespace: str | None = None, doc_version: str | None = None) -> int:
        store = self._store_provider.get_default_store()
        deleted = store.delete_by_doc_name(doc_name=doc_name, namespace=namespace, doc_version=doc_version)
        ns = namespace or "__all__"
        if deleted > 0:
            RAG_DOC_DELETE_COUNT.labels(namespace=ns).inc(deleted)
        return deleted

    def delete_by_namespace(self, namespace: str | None) -> int:
        store = self._store_provider.get_default_store()
        deleted = store.delete_by_namespace(namespace)
        ns = namespace if namespace is not None else "__default__"
        if deleted > 0:
            RAG_DOC_DELETE_COUNT.labels(namespace=ns).inc(deleted)
        return deleted

    def reassign_namespace_for_doc(
        self,
        doc_name: str,
        from_namespace: str | None,
        to_namespace: str | None,
        doc_version: str | None = None,
    ) -> int:
        store = self._store_provider.get_default_store()
        return store.reassign_namespace_for_doc(
            doc_name=doc_name,
            from_namespace=from_namespace,
            to_namespace=to_namespace,
            doc_version=doc_version,
        )

    def update_namespace_kb_config(
        self,
        namespace: str | None,
        *,
        enabled: bool,
        priority: int,
        doc_names: Sequence[str] | None = None,
    ) -> int:
        store = self._store_provider.get_default_store()
        return store.update_namespace_kb_config(
            namespace,
            enabled=enabled,
            priority=priority,
            doc_names=doc_names,
        )

    @staticmethod
    def _rrf_fuse(
        semantic_hits: list[dict],
        keyword_hits: list[dict],
        rrf_k: int,
        metadata_hits: list[dict] | None = None,
    ) -> list[dict]:
        scored: dict[str, dict] = {}

        def upsert(hit: dict, rank: int, source: str) -> None:
            ext_id = hit.get("ext_id") or hit.get("text")
            if not ext_id:
                return
            key = str(ext_id)
            base = scored.get(key)
            inc = 1.0 / float(rrf_k + rank + 1)
            if base is None:
                item = dict(hit)
                item["_fused_score"] = inc
                item["_sources"] = [source]
                scored[key] = item
                return
            base["_fused_score"] = float(base.get("_fused_score", 0.0)) + inc
            srcs = base.get("_sources") or []
            if source not in srcs:
                srcs.append(source)
            base["_sources"] = srcs

        for idx, hit in enumerate(semantic_hits):
            upsert(hit, idx, "semantic")
        for idx, hit in enumerate(keyword_hits):
            upsert(hit, idx, "keyword")
        for idx, hit in enumerate(metadata_hits or []):
            upsert(hit, idx, "metadata")
        items = list(scored.values())
        items.sort(key=lambda x: float(x.get("_fused_score", 0.0)), reverse=True)
        return items

    def _rerank(self, query: str, hits: list[dict]) -> list[dict]:
        if not hits:
            return []
        reranker = self._get_reranker()
        if reranker is None:
            # 跳过重排：保持融合顺序，避免流式/推理接口因 reranker 加载失败直接中断。
            return hits
        q = (query or "").strip()
        if not q:
            return hits

        pairs: list[list[str]] = []
        scored_indices: list[int] = []
        for idx, hit in enumerate(hits):
            doc = str(hit.get("text") or "").strip()
            if doc:
                pairs.append([q, doc])
                scored_indices.append(idx)
        if not pairs:
            logger.warning("RAGService rerank skipped: all candidate texts empty hits=%s", len(hits))
            return hits

        t0 = time.perf_counter()
        try:
            scores = reranker.predict(pairs, batch_size=min(16, len(pairs)))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "RAGService rerank batch predict failed; retry batch_size=1. pairs=%s err=%s",
                len(pairs),
                e,
            )
            try:
                scores = reranker.predict(pairs, batch_size=1)
            except Exception as e2:  # noqa: BLE001
                logger.warning(
                    "RAGService rerank predict failed; keep RRF order. pairs=%s err=%s",
                    len(pairs),
                    e2,
                    exc_info=True,
                )
                return hits
        rerank_ms = int((time.perf_counter() - t0) * 1000)
        target_device = _cross_encoder_device_repr(reranker)
        logger.info(
            "RAGService rerank done pairs=%s rerank_ms=%s device=%s",
            len(pairs),
            rerank_ms,
            target_device,
        )
        RAG_RERANK_COUNT.inc()
        for pair_idx, hit_idx in enumerate(scored_indices):
            hits[hit_idx]["_rerank_score"] = float(scores[pair_idx])
        hits.sort(key=lambda x: float(x.get("_rerank_score", 0.0)), reverse=True)
        return hits

    def _get_scene_profile(self, scene: str | None):
        if not scene:
            return None
        profiles = self._cfg.scene_profiles
        if scene == "llm_inference":
            return profiles.llm_inference
        if scene == "chatbot":
            return profiles.chatbot
        if scene == "analysis":
            return profiles.analysis
        if scene == "nl2sql":
            return profiles.nl2sql
        return None

