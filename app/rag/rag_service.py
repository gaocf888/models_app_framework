from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import List, Sequence

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.core.metrics import (
    RAG_DOC_DELETE_COUNT,
    RAG_KEYWORD_RECALL_COUNT,
    RAG_QUERY_COUNT,
    RAG_RERANK_COUNT,
    RAG_SEMANTIC_RECALL_COUNT,
)
from app.rag.embedding_service import EmbeddingService
from app.rag.vector_store import VectorStoreProvider

logger = get_logger(__name__)


class RAGService:
    """
    统一的 RAG 检索服务。

    当前版本：
    - 使用 EmbeddingService 生成嵌入；
    - 使用 VectorStoreProvider 提供的向量库做余弦相似度检索；
    - RAG 策略参数（如 top_k）从 AppConfig.rag 中读取。
    """

    def __init__(self, embedding_service: EmbeddingService | None = None, store_provider: VectorStoreProvider | None = None) -> None:
        self._cfg = get_app_config().rag
        self._embedding_service = embedding_service or EmbeddingService()
        self._store_provider = store_provider or VectorStoreProvider()
        self._reranker = None

    def _get_reranker(self):
        if self._reranker is not None:
            return self._reranker
        model_path = self._cfg.hybrid.reranker_model_path
        model_name = model_path or self._cfg.hybrid.reranker_model_name
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import-untyped]
        except Exception as e:  # noqa: BLE001
            raise ImportError(
                "CrossEncoder reranker requires sentence-transformers. "
                "Install with: pip install -r requirements-大模型应用.txt"
            ) from e
        self._reranker = CrossEncoder(model_name)
        logger.info("RAGService loaded CrossEncoder reranker: %s", model_name)
        return self._reranker

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

    def retrieve_context(
        self,
        query: str,
        top_k: int | None = None,
        namespace: str | None = None,
        use_hybrid: bool | None = None,
        scene: str | None = None,
    ) -> List[str]:
        """
        执行相似度检索，返回候选上下文文本列表。
        """
        RAG_QUERY_COUNT.inc()
        profile = self._get_scene_profile(scene)
        k = top_k or (profile.top_k if profile is not None else self._cfg.top_k)
        store = self._store_provider.get_default_store()
        q_emb = self._embedding_service.embed_text(query)
        hybrid_enabled = self._cfg.hybrid.enabled if use_hybrid is None else use_hybrid
        if not hybrid_enabled:
            RAG_SEMANTIC_RECALL_COUNT.inc()
            results = store.similarity_search_by_vector(q_emb, k=k, namespace=namespace)
            return [r.get("text", "") for r in results if r.get("text")]

        sem_top = profile.semantic_top_k if profile is not None else self._cfg.hybrid.semantic_top_k
        kw_top = profile.keyword_top_k if profile is not None else self._cfg.hybrid.keyword_top_k
        sem_k = max(sem_top, k)
        kw_k = max(kw_top, k)
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_sem = pool.submit(store.similarity_search_by_vector, q_emb, sem_k, namespace)
            f_kw = pool.submit(store.keyword_search, query, kw_k, namespace)
            semantic_hits = f_sem.result()
            keyword_hits = f_kw.result()
        RAG_SEMANTIC_RECALL_COUNT.inc()
        RAG_KEYWORD_RECALL_COUNT.inc()

        fused = self._rrf_fuse(
            semantic_hits=semantic_hits,
            keyword_hits=keyword_hits,
            rrf_k=self._cfg.hybrid.rrf_k,
        )
        rerank_base = profile.rerank_top_n if profile is not None else self._cfg.hybrid.rerank_top_n
        rerank_top_n = max(rerank_base, k)
        candidates = fused[:rerank_top_n]
        reranked = self._rerank(query=query, hits=candidates)
        return [h.get("text", "") for h in reranked[:k] if h.get("text")]

    def delete_by_doc_name(self, doc_name: str, namespace: str | None = None) -> int:
        store = self._store_provider.get_default_store()
        deleted = store.delete_by_doc_name(doc_name=doc_name, namespace=namespace)
        ns = namespace or "__all__"
        if deleted > 0:
            RAG_DOC_DELETE_COUNT.labels(namespace=ns).inc(deleted)
        return deleted

    @staticmethod
    def _rrf_fuse(
        semantic_hits: list[dict],
        keyword_hits: list[dict],
        rrf_k: int,
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
        items = list(scored.values())
        items.sort(key=lambda x: float(x.get("_fused_score", 0.0)), reverse=True)
        return items

    def _rerank(self, query: str, hits: list[dict]) -> list[dict]:
        if not hits:
            return []
        pairs = [[query, h.get("text", "")] for h in hits]
        reranker = self._get_reranker()
        scores = reranker.predict(pairs)
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

