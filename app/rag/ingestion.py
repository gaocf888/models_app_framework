from __future__ import annotations

"""
RAG 知识摄入服务（RAGIngestionService）。

对应《下一阶段工作清单》中 TODO-P6：
- 负责文档/Schema/业务知识/问答样例等的摄入与索引构建；
- 与 EmbeddingService、VectorStoreProvider 协同工作，并支持可选 GraphRAG 摄入。

当前实现：
- 提供内存级别的“数据集”登记与文本摄入能力；
- 支持按文档名更新（同名先删后灌）；
- 实际项目中可扩展为将数据集元信息持久化到数据库/配置中心。
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.graph.ingestion import GraphIngestionService
from app.rag.embedding_service import EmbeddingService
from app.rag.rag_service import RAGService
from app.rag.vector_store import VectorStoreProvider

logger = get_logger(__name__)


@dataclass
class RAGDatasetMeta:
    dataset_id: str
    description: str | None = None
    num_items: int = 0
    namespace: Optional[str] = None
    doc_name: Optional[str] = None


class RAGIngestionService:
    def __init__(
        self,
        embedding_service: EmbeddingService | None = None,
        store_provider: VectorStoreProvider | None = None,
        graph_ingestion: GraphIngestionService | None = None,
    ) -> None:
        self._embedding_service = embedding_service or EmbeddingService()
        self._store_provider = store_provider or VectorStoreProvider()
        self._rag_service = RAGService(embedding_service=self._embedding_service, store_provider=self._store_provider)
        self._datasets: Dict[str, RAGDatasetMeta] = {}

        # 按配置决定是否启用 GraphRAG 摄入
        cfg = get_app_config().rag  # type: ignore[attr-defined]
        if graph_ingestion is not None:
            self._graph_ingestion = graph_ingestion
        else:
            # 如果配置开启了 GraphRAG，则尝试初始化默认 GraphIngestionService
            try:
                self._graph_ingestion = GraphIngestionService(cfg.graph) if cfg.graph.enabled else None
            except Exception as e:
                logger.warning("failed to initialize GraphIngestionService: %s", e, exc_info=True)
                self._graph_ingestion = None

    def ingest_texts(
        self,
        dataset_id: str,
        texts: List[str],
        description: str | None = None,
        namespace: str | None = None,
        doc_name: str | None = None,
        replace_if_exists: bool = True,
        doc_version: str = "v1",
        tenant_id: str | None = None,
        run_post_hook: bool = True,
    ) -> None:
        """
        将一批文本摄入 RAG 知识库（向量+全文），并登记为指定数据集。
        """
        store = self._store_provider.get_default_store()
        effective_doc_name = doc_name or dataset_id

        if replace_if_exists:
            deleted = store.delete_by_doc_name(doc_name=effective_doc_name, namespace=namespace)
            if deleted > 0:
                logger.info(
                    "deleted %s existing chunks before re-ingest, doc_name=%s namespace=%s",
                    deleted,
                    effective_doc_name,
                    namespace,
                )

        embs = self._embedding_service.embed_texts(texts)
        metas = [{"doc_version": doc_version, "tenant_id": tenant_id} for _ in texts]
        store.add_texts(
            texts,
            embeddings=embs,
            namespace=namespace,
            doc_name=effective_doc_name,
            metadatas=metas,
        )

        if run_post_hook:
            self.post_index_hook(
                dataset_id=dataset_id,
                texts=texts,
                namespace=namespace,
                doc_name=effective_doc_name,
                doc_version=doc_version,
                replace_if_exists=replace_if_exists,
            )
        meta = self._datasets.get(dataset_id) or RAGDatasetMeta(
            dataset_id=dataset_id,
            description=description,
            namespace=namespace,
            doc_name=effective_doc_name,
        )
        meta.num_items = len(texts)
        if description:
            meta.description = description
        if namespace:
            meta.namespace = namespace
        meta.doc_name = effective_doc_name
        self._datasets[dataset_id] = meta
        logger.info(
            "ingested %s texts into RAG dataset=%s namespace=%s doc_name=%s",
            len(texts),
            dataset_id,
            namespace,
            effective_doc_name,
        )

    def list_datasets(self) -> List[RAGDatasetMeta]:
        return list(self._datasets.values())

    def reassign_namespace_for_doc(
        self,
        doc_name: str,
        from_namespace: str | None,
        to_namespace: str | None,
        doc_version: str | None = None,
    ) -> int:
        return self._rag_service.reassign_namespace_for_doc(
            doc_name=doc_name,
            from_namespace=from_namespace,
            to_namespace=to_namespace,
            doc_version=doc_version,
        )

    def delete_by_doc_name(
        self, doc_name: str, namespace: str | None = None, doc_version: str | None = None
    ) -> int:
        deleted = self._rag_service.delete_by_doc_name(doc_name=doc_name, namespace=namespace, doc_version=doc_version)
        if getattr(self, "_graph_ingestion", None) is not None:
            try:
                self._graph_ingestion.delete_document(  # type: ignore[call-arg]
                    doc_name=doc_name, namespace=namespace, doc_version=doc_version
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "GraphIngestionService.delete_document failed for doc_name=%s namespace=%s doc_version=%s: %s",
                    doc_name,
                    namespace,
                    doc_version,
                    e,
                    exc_info=True,
                )
        return deleted

    def post_index_hook(
        self,
        dataset_id: str,
        texts: List[str],
        namespace: str | None,
        doc_name: str,
        doc_version: str,
        replace_if_exists: bool,
    ) -> None:
        """
        摄入后钩子：用于承接图侧写入、后续审计/通知等扩展能力。
        """
        if getattr(self, "_graph_ingestion", None) is not None:
            try:
                self._graph_ingestion.ingest_from_chunks(  # type: ignore[call-arg]
                    dataset_id=dataset_id,
                    texts=texts,
                    namespace=namespace,
                    doc_name=doc_name,
                    doc_version=doc_version,
                    replace_if_exists=replace_if_exists,
                )
            except Exception as e:
                # 为避免影响主 RAG 流程，此处仅记录告警，不抛出
                logger.warning(
                    "GraphIngestionService.ingest_from_chunks failed for dataset=%s doc=%s: %s",
                    dataset_id,
                    doc_name,
                    e,
                    exc_info=True,
                )

    def finalize_alias_version(self, namespace: str | None = None, doc_version: str | None = None) -> None:
        """
        finalize 阶段扩展点：用于后续接入 alias/version 切换、回写审计等治理动作。
        当前版本默认 no-op，保留企业级阶段语义。
        """
        logger.debug("finalize_alias_version noop: namespace=%s doc_version=%s", namespace, doc_version)

    def query(self, query: str, top_k: int | None = None, namespace: str | None = None, scene: str = "llm_inference") -> List[str]:
        return self._rag_service.retrieve_context(
            query=query,
            top_k=top_k,
            namespace=namespace,
            scene=scene,
        )

