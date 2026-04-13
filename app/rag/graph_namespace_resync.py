from __future__ import annotations

"""
namespace 迁移后的 GraphRAG 异步补偿：删除旧 namespace 下图数据，并按新 namespace 重灌。
由 `POST /rag/documents/namespace/move` 在响应返回后通过 BackgroundTasks 触发。
"""

from app.core.config import get_app_config
from app.core.logging import get_logger
from app.rag.ingestion import RAGIngestionService

logger = get_logger(__name__)


def run_graph_resync_after_namespace_move(
    *,
    doc_name: str,
    from_namespace: str | None,
    to_namespace: str | None,
    doc_version: str | None,
    dataset_id: str,
) -> None:
    """
    1) 从向量库（已迁到新 namespace）拉取该文档全部 chunk 文本；
    2) 删除图库中旧 namespace 下该文档（及指定版本）的 DocumentChunk 等；
    3) 若拉取到正文，则在新 namespace 下 ingest_from_chunks 重建图。
    """
    try:
        cfg = get_app_config().rag
        if not cfg.graph.enabled:
            return
        ds = (dataset_id or "").strip()
        if not ds:
            logger.warning("graph namespace resync skipped: empty dataset_id doc=%s", doc_name)
            return

        ingestion = RAGIngestionService()
        graph = getattr(ingestion, "_graph_ingestion", None)
        if graph is None:
            logger.info("graph namespace resync skipped: graph ingestion unavailable doc=%s", doc_name)
            return

        store = ingestion._rag_service._store_provider.get_default_store()
        texts = store.list_chunk_texts_for_document(
            doc_name=doc_name,
            namespace=to_namespace,
            doc_version=doc_version,
        )

        graph.delete_document(doc_name=doc_name, namespace=from_namespace, doc_version=doc_version)

        if not texts:
            logger.warning(
                "graph namespace resync: no vector chunks for doc=%s namespace=%s (old graph partition removed only)",
                doc_name,
                to_namespace,
            )
            return

        graph.ingest_from_chunks(
            dataset_id=ds,
            texts=texts,
            namespace=to_namespace,
            doc_name=doc_name,
            doc_version=doc_version or "v1",
            replace_if_exists=True,
        )
        logger.info(
            "graph namespace resync completed doc=%s chunks=%s to_namespace=%s",
            doc_name,
            len(texts),
            to_namespace,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "graph namespace resync failed doc=%s from_namespace=%s to_namespace=%s",
            doc_name,
            from_namespace,
            to_namespace,
        )
