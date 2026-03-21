from __future__ import annotations

from typing import List, Sequence

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.embedding_service import EmbeddingService
from app.rag.vector_store import VectorStoreProvider
from app.core.metrics import RAG_QUERY_COUNT

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

    def index_texts(self, texts: Sequence[str]) -> None:
        """
        将一批文本加入默认向量库。
        说明：真正生产环境中，这通常在离线摄入流程（RAGIngestionService）中调用。
        """
        embs = self._embedding_service.embed_texts(list(texts))
        store = self._store_provider.get_default_store()
        store.add_texts(texts, embeddings=embs)

    def retrieve_context(self, query: str, top_k: int | None = None) -> List[str]:
        """
        执行相似度检索，返回候选上下文文本列表。
        """
        RAG_QUERY_COUNT.inc()
        k = top_k or self._cfg.top_k
        store = self._store_provider.get_default_store()
        q_emb = self._embedding_service.embed_text(query)
        results = store.similarity_search_by_vector(q_emb, k=k)
        return [text for text, _score in results]

