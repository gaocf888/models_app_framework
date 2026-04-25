from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Sequence

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
from app.rag.embedding_service import EmbeddingService
from app.rag.models import RetrievedChunk
from app.rag.vector_store import VectorStoreProvider

logger = get_logger(__name__)


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
        self._embedding_service = embedding_service or EmbeddingService()
        self._store_provider = store_provider or VectorStoreProvider()
        self._reranker = None
        self._reranker_lock = threading.Lock()

    def _get_reranker(self):
        if self._reranker is not None:
            return self._reranker
        with self._reranker_lock:
            if self._reranker is not None:
                return self._reranker
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
            try:
                from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]
            except Exception as e:  # noqa: BLE001
                raise ImportError(
                    "CrossEncoder reranker requires sentence-transformers. "
                    "Install with: pip install -r requirements-大模型应用.txt"
                ) from e
            try:
                common_kwargs = {
                    "trust_remote_code": os.getenv("RAG_RERANKER_TRUST_REMOTE_CODE", "false").lower() == "true",
                }
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
                    "RAGService loaded CrossEncoder reranker: %s device=%s configured_device=%s",
                    load_id,
                    target_device,
                    configured_device or "auto",
                )
                return self._reranker
            except Exception as e:  # noqa: BLE001
                # Reranker 不是摄入/检索链路的强依赖：模型缺失/无网时允许跳过重排。
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
        score = None
        if hit.get("_rerank_score") is not None:
            score = float(hit["_rerank_score"])
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
    ) -> List[RetrievedChunk]:
        """
        执行检索并返回标准 RetrievedChunk 列表（设计稿 §E 统一检索输出）。
        """
        RAG_QUERY_COUNT.inc()
        profile = self._get_scene_profile(scene)
        k = top_k or (profile.top_k if profile is not None else self._cfg.top_k)
        pv = self._cfg.ingestion.pipeline_version
        store = self._store_provider.get_default_store()
        q_emb = self._embedding_service.embed_text(query)
        hybrid_enabled = self._cfg.hybrid.enabled if use_hybrid is None else use_hybrid
        hits: list[dict]
        if not hybrid_enabled:
            RAG_SEMANTIC_RECALL_COUNT.inc()
            hits = store.similarity_search_by_vector(q_emb, k=k, namespace=namespace)
        else:
            sem_top = profile.semantic_top_k if profile is not None else self._cfg.hybrid.semantic_top_k
            kw_top = profile.keyword_top_k if profile is not None else self._cfg.hybrid.keyword_top_k
            md_top = self._cfg.hybrid.metadata_top_k
            sem_k = max(sem_top, k)
            kw_k = max(kw_top, k)
            md_k = max(md_top, k)
            metadata_enabled = bool(self._cfg.hybrid.metadata_recall_enabled)
            worker_num = 3 if metadata_enabled else 2
            with ThreadPoolExecutor(max_workers=worker_num) as pool:
                f_sem = pool.submit(store.similarity_search_by_vector, q_emb, sem_k, namespace)
                f_kw = pool.submit(store.keyword_search, query, kw_k, namespace)
                f_md = pool.submit(store.metadata_search, query, md_k, namespace) if metadata_enabled else None
                semantic_hits = f_sem.result()
                keyword_hits = f_kw.result()
                metadata_hits = f_md.result() if f_md is not None else []
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
            hits = self._rerank(query=query, hits=candidates)[:k]

        out: List[RetrievedChunk] = []
        for h in hits:
            if not h.get("text"):
                continue
            out.append(self._hit_to_chunk(h, pv))
            if len(out) >= k:
                break
        return out

    def retrieve_context(
        self,
        query: str,
        top_k: int | None = None,
        namespace: str | None = None,
        use_hybrid: bool | None = None,
        scene: str | None = None,
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
        )
        return [c.text for c in chunks if c.text]

    def delete_by_doc_name(self, doc_name: str, namespace: str | None = None, doc_version: str | None = None) -> int:
        store = self._store_provider.get_default_store()
        deleted = store.delete_by_doc_name(doc_name=doc_name, namespace=namespace, doc_version=doc_version)
        ns = namespace or "__all__"
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
        t0 = time.perf_counter()
        pairs = [[query, h.get("text", "")] for h in hits]
        scores = reranker.predict(pairs)
        rerank_ms = int((time.perf_counter() - t0) * 1000)
        target_device = _cross_encoder_device_repr(reranker)
        logger.info(
            "RAGService rerank done pairs=%s rerank_ms=%s device=%s",
            len(pairs),
            rerank_ms,
            target_device,
        )
        RAG_RERANK_COUNT.inc()
        for idx, hit in enumerate(hits):
            hit["_rerank_score"] = float(scores[idx])
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

