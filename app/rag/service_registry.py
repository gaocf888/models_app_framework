"""
RAG 基座进程内单例注册表。

EmbeddingService / RAGService / VectorStoreProvider 在单 worker 内只初始化一份，
避免 models-app 启动时多 API 模块重复加载同一 embed/rerank 权重。

显式构造函数注入（测试 Fake、定制部署）时 bypass 本注册表。
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.nl2sql.rag_service import NL2SQLRAGService
    from app.rag.embedding_service import EmbeddingService
    from app.rag.hybrid_rag_service import HybridRAGService
    from app.rag.rag_service import RAGService
    from app.rag.vector_store import VectorStoreProvider

logger = get_logger(__name__)

_lock = threading.RLock()
_embedding_service: EmbeddingService | None = None
_rag_service: RAGService | None = None
_vector_store_provider: VectorStoreProvider | None = None
_nl2sql_rag_service: NL2SQLRAGService | None = None
_hybrid_rag_service: HybridRAGService | None = None


def get_embedding_service() -> EmbeddingService:
    """进程内唯一 EmbeddingService（懒加载，线程安全）。"""
    global _embedding_service
    if _embedding_service is not None:
        return _embedding_service
    with _lock:
        if _embedding_service is None:
            from app.rag.embedding_service import EmbeddingService

            logger.info("RAG registry: initializing singleton EmbeddingService (may take a while on first load)...")
            _embedding_service = EmbeddingService()
            logger.info(
                "RAG registry: created singleton EmbeddingService id=%s",
                id(_embedding_service),
            )
        return _embedding_service


def get_vector_store_provider() -> VectorStoreProvider:
    """进程内唯一 VectorStoreProvider（懒加载，线程安全）。"""
    global _vector_store_provider
    if _vector_store_provider is not None:
        return _vector_store_provider
    with _lock:
        if _vector_store_provider is None:
            from app.rag.vector_store import VectorStoreProvider

            _vector_store_provider = VectorStoreProvider()
            logger.info(
                "RAG registry: created singleton VectorStoreProvider id=%s",
                id(_vector_store_provider),
            )
        return _vector_store_provider


def get_rag_service() -> RAGService:
    """进程内唯一 RAGService（共享 embed、store 与 lazy reranker）。"""
    global _rag_service
    if _rag_service is not None:
        return _rag_service
    with _lock:
        if _rag_service is None:
            from app.rag.rag_service import RAGService

            logger.info("RAG registry: initializing singleton RAGService...")
            _rag_service = RAGService(
                embedding_service=get_embedding_service(),
                store_provider=get_vector_store_provider(),
            )
            logger.info(
                "RAG registry: created singleton RAGService id=%s",
                id(_rag_service),
            )
        return _rag_service


def get_nl2sql_rag_service() -> NL2SQLRAGService:
    """进程内唯一 NL2SQLRAGService（包装同一 RAGService）。"""
    global _nl2sql_rag_service
    if _nl2sql_rag_service is not None:
        return _nl2sql_rag_service
    with _lock:
        if _nl2sql_rag_service is None:
            from app.nl2sql.rag_service import NL2SQLRAGService

            _nl2sql_rag_service = NL2SQLRAGService(rag_service=get_rag_service())
            logger.info(
                "RAG registry: created singleton NL2SQLRAGService id=%s",
                id(_nl2sql_rag_service),
            )
        return _nl2sql_rag_service


def get_hybrid_rag_service() -> HybridRAGService:
    """进程内唯一 HybridRAGService（包装同一 RAGService）。"""
    global _hybrid_rag_service
    if _hybrid_rag_service is not None:
        return _hybrid_rag_service
    with _lock:
        if _hybrid_rag_service is None:
            from app.rag.hybrid_rag_service import HybridRAGService

            _hybrid_rag_service = HybridRAGService(rag_service=get_rag_service())
            logger.info(
                "RAG registry: created singleton HybridRAGService id=%s",
                id(_hybrid_rag_service),
            )
        return _hybrid_rag_service


def clear_rag_service_registry() -> None:
    """清空注册表（仅测试/fixture 使用，避免用例间单例泄漏）。"""
    global _embedding_service, _rag_service, _vector_store_provider
    global _nl2sql_rag_service, _hybrid_rag_service
    with _lock:
        _embedding_service = None
        _rag_service = None
        _vector_store_provider = None
        _nl2sql_rag_service = None
        _hybrid_rag_service = None
