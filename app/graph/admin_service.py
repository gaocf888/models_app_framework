from __future__ import annotations

"""
GraphAdminService：知识图谱运维（统计、健康、重建、Schema 热加载）。
"""

from typing import Any

from app.core.config import GraphRAGConfig, get_app_config
from app.core.logging import get_logger
from app.graph.client import Neo4jGraphClient
from app.graph.ingestion import GraphIngestionService
from app.graph.query_service import GraphQueryService
from app.graph.schema_loader import reload_graph_schema, schema_summary
from app.rag.document_repository import DocumentRepository
from app.rag.vector_store import VectorStoreProvider

logger = get_logger(__name__)

_DOC_LIST_PAGE = 500


class GraphAdminService:
    def __init__(self, cfg: GraphRAGConfig | None = None) -> None:
        app_cfg = get_app_config()
        self._cfg = cfg or app_cfg.rag.graph  # type: ignore[attr-defined]
        self._client = Neo4jGraphClient(self._cfg)

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled)

    def health(self) -> dict[str, Any]:
        if not self._cfg.enabled:
            return {"ok": False, "enabled": False, "reason": "GRAPH_RAG_ENABLED=false"}
        ping = self._client.ping()
        return {
            "ok": bool(ping.get("ok")),
            "enabled": True,
            "neo4j_uri": self._cfg.uri,
            "ingest_on_rag": self._cfg.ingest_on_rag,
            "delete_on_rag": self._cfg.delete_on_rag,
            "extraction_mode": self._cfg.extraction_mode,
            "detail": ping,
        }

    def stats(self, namespace: str | None = None) -> dict[str, Any]:
        if not self._cfg.enabled:
            return {"enabled": False}
        ns_filter = ""
        params: dict[str, Any] = {}
        if namespace:
            ns_filter = " WHERE n.namespace = $namespace "
            params["namespace"] = namespace
        node_rows = self._client.run_cypher(
            f"MATCH (n) {ns_filter} RETURN labels(n) AS labels, count(*) AS cnt",
            params,
        )
        rel_rows = self._client.run_cypher(
            "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(*) AS cnt",
            {},
        )
        chunk_count = 0
        entity_count = 0
        if namespace:
            chunk_rows = self._client.run_cypher(
                "MATCH (d:DocumentChunk {namespace: $namespace}) RETURN count(d) AS cnt",
                {"namespace": namespace},
            )
            entity_rows = self._client.run_cypher(
                "MATCH (e:Entity {namespace: $namespace}) RETURN count(e) AS cnt",
                {"namespace": namespace},
            )
            chunk_count = int((chunk_rows[0].get("cnt") if chunk_rows else 0) or 0)
            entity_count = int((entity_rows[0].get("cnt") if entity_rows else 0) or 0)
        return {
            "enabled": True,
            "namespace": namespace,
            "nodes_by_label": node_rows,
            "relations_by_type": rel_rows,
            "document_chunks": chunk_count,
            "entities": entity_count,
            "schema": schema_summary(self._cfg.schema),
        }

    def get_schema(self) -> dict[str, Any]:
        return {
            "enabled": self._cfg.enabled,
            "schema_config_path": self._cfg.schema_config_path or "configs/graph_schema.yaml",
            "schema_hot_reload": self._cfg.schema_hot_reload,
            "schema": schema_summary(self._cfg.schema),
        }

    def reload_schema(self) -> dict[str, Any]:
        if not self._cfg.enabled:
            raise RuntimeError("GraphRAG is disabled")
        if not self._cfg.schema_hot_reload:
            raise RuntimeError("GRAPH_SCHEMA_HOT_RELOAD is false")
        schema = reload_graph_schema(self._cfg)
        return schema_summary(schema)

    def rebuild(
        self,
        *,
        mode: str = "full",
        namespace: str | None = None,
        doc_names: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self._cfg.enabled:
            raise RuntimeError("GraphRAG is disabled")
        ingestion = GraphIngestionService(self._cfg)
        store = VectorStoreProvider().get_default_store()
        doc_repo = DocumentRepository()
        rebuilt_docs = 0
        rebuilt_chunks = 0
        skipped_docs = 0

        targets: list[dict[str, Any]] = []
        if doc_names:
            for name in doc_names:
                targets.append({"doc_name": name, "namespace": namespace, "doc_version": None, "dataset_id": None})
        elif mode == "full":
            targets = self._list_all_documents(doc_repo, namespace=namespace)
        else:
            raise RuntimeError("incremental rebuild requires doc_names")

        if not targets:
            return {
                "mode": mode,
                "namespace": namespace,
                "rebuilt_docs": 0,
                "rebuilt_chunks": 0,
                "skipped_docs": 0,
                "message": "no documents to rebuild",
            }

        for rec in targets:
            doc_name = str(rec.get("doc_name") or "")
            if not doc_name:
                continue
            ns = rec.get("namespace") if rec.get("namespace") is not None else namespace
            doc_version = rec.get("doc_version")
            dataset_id = str(rec.get("dataset_id") or doc_name)
            texts = store.list_chunk_texts_for_document(
                doc_name=doc_name,
                namespace=ns,
                doc_version=doc_version,
            )
            if not texts:
                skipped_docs += 1
                logger.warning(
                    "graph rebuild skipped doc=%s namespace=%s: no vector chunks",
                    doc_name,
                    ns,
                )
                continue
            effective_version = str(doc_version or "v1")
            ingestion.ingest_from_chunks(
                dataset_id=dataset_id,
                texts=texts,
                namespace=ns,
                doc_name=doc_name,
                doc_version=effective_version,
                replace_if_exists=True,
            )
            rebuilt_docs += 1
            rebuilt_chunks += len(texts)

        return {
            "mode": mode,
            "namespace": namespace,
            "rebuilt_docs": rebuilt_docs,
            "rebuilt_chunks": rebuilt_chunks,
            "skipped_docs": skipped_docs,
        }

    def _list_all_documents(
        self,
        doc_repo: DocumentRepository,
        *,
        namespace: str | None,
    ) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = doc_repo.list(limit=_DOC_LIST_PAGE, offset=offset, namespace=namespace)
            if not page:
                break
            out.extend(page)
            if len(page) < _DOC_LIST_PAGE:
                break
            offset += _DOC_LIST_PAGE
        return out

    def delete_document(
        self,
        doc_name: str,
        namespace: str | None = None,
        doc_version: str | None = None,
    ) -> dict[str, Any]:
        if not self._cfg.enabled:
            raise RuntimeError("GraphRAG is disabled")
        GraphIngestionService(self._cfg).delete_document(
            doc_name=doc_name,
            namespace=namespace,
            doc_version=doc_version,
        )
        return {"deleted": True, "doc_name": doc_name, "namespace": namespace, "doc_version": doc_version}

    def debug_query(self, question: str, namespace: str | None = None) -> dict[str, Any]:
        if not self._cfg.enabled:
            raise RuntimeError("GraphRAG is disabled")
        facts = GraphQueryService(self._cfg).query_relevant_facts(question, namespace=namespace)
        return {"question": question, "namespace": namespace, "facts": facts, "count": len(facts)}
