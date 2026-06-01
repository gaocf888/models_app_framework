from __future__ import annotations

"""
GraphIngestionService

负责在 RAG 摄入阶段，将文本分片转换为图结构（实体 + 关系），并写入图数据库。
默认使用 LLM 抽取；规则抽取仅作可配置回退。
"""

from typing import Any, List, Optional

from app.core.config import GraphRAGConfig, get_app_config
from app.core.logging import get_logger
from app.graph.client import Neo4jGraphClient
from app.graph.extraction.llm_extractor import LLMGraphExtractor
from app.graph.extraction.rule_extractor import RuleGraphExtractor
from app.graph.extraction.types import ExtractedEntity, ExtractedGraphPayload, ExtractedRelation

logger = get_logger(__name__)


class GraphIngestionService:
    """
    GraphRAG 摄入服务：将文本分片转换为图结构并写入 Neo4j。
    """

    def __init__(self, cfg: GraphRAGConfig | None = None) -> None:
        app_cfg = get_app_config()
        self._cfg = cfg or app_cfg.rag.graph  # type: ignore[attr-defined]
        self._client = Neo4jGraphClient(self._cfg)

        if not self._cfg.enabled:
            logger.info("GraphIngestionService initialized but GraphRAG is disabled.")
            return

        self._llm_extractor = LLMGraphExtractor(self._cfg)
        self._rule_extractor = RuleGraphExtractor(self._cfg)
        logger.info(
            "GraphIngestionService ready (extraction_mode=%s, uri=%s).",
            self._cfg.extraction_mode,
            self._cfg.uri,
        )

    # Public API -------------------------------------------------------------

    def ingest_from_chunks(
        self,
        dataset_id: str,
        texts: List[str],
        namespace: Optional[str] = None,
        doc_name: str | None = None,
        doc_version: str = "v1",
        replace_if_exists: bool = True,
    ) -> None:
        """从一批文本分片中抽取实体与关系并写入图数据库。"""
        if not self._cfg.enabled:
            return
        if not texts:
            return

        logger.info(
            "GraphIngestionService: ingest %s chunks into graph (dataset=%s, namespace=%s)",
            len(texts),
            dataset_id,
            namespace,
        )
        ns = namespace or "__default__"
        effective_doc_name = doc_name or dataset_id
        doc_key = self._build_doc_key(ns, effective_doc_name, doc_version)
        if replace_if_exists:
            self.delete_document(doc_name=effective_doc_name, namespace=ns, doc_version=doc_version)

        schema = self._cfg.schema
        mode = (self._cfg.extraction_mode or "llm").lower()
        if mode == "llm":
            payloads = self._extract_payloads_llm(texts, schema=schema)
        else:
            payloads = [self._rule_extractor.extract(t, schema=schema) for t in texts]

        for idx, chunk_text in enumerate(texts):
            payload = payloads[idx] if idx < len(payloads) else ExtractedGraphPayload()
            chunk_id = f"{dataset_id}:{ns}:{effective_doc_name}:{doc_version}:{idx}"
            self._upsert_chunk(
                dataset_id=dataset_id,
                namespace=ns,
                chunk_id=chunk_id,
                text=chunk_text,
                doc_name=effective_doc_name,
                doc_version=doc_version,
                doc_key=doc_key,
            )
            if not payload.entities:
                continue
            for ent in payload.entities:
                self._upsert_entity(ns, ent)
                self._link_chunk_entity(ns=ns, chunk_id=chunk_id, entity_id=ent.id or "")
            for rel in payload.relations:
                self._link_entity_relation(ns, rel)
            if mode == "rule" and len(payload.entities) >= 2:
                for i in range(len(payload.entities)):
                    for j in range(i + 1, len(payload.entities)):
                        self._link_cooccur(ns, payload.entities[i].id or "", payload.entities[j].id or "")

    def _extract_payloads_llm(
        self,
        texts: list[str],
        schema: Any,
    ) -> list[ExtractedGraphPayload]:
        """LLM 批量抽取；批量失败时逐条抽取并应用回退策略。"""
        try:
            return self._llm_extractor.extract_batch(texts, schema=schema)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM batch extraction failed, falling back per-chunk: %s", exc)
        return [self._extract_payload(text) for text in texts]

    def delete_document(self, doc_name: str, namespace: Optional[str] = None, doc_version: str | None = None) -> None:
        """删除图侧文档相关节点与关系。"""
        if not self._cfg.enabled:
            return
        ns = namespace or "__default__"
        if doc_version:
            doc_key = self._build_doc_key(ns, doc_name, doc_version)
            self._cypher(
                """
                MATCH (d:DocumentChunk {namespace: $namespace, doc_key: $doc_key})
                DETACH DELETE d
                """,
                {"namespace": ns, "doc_key": doc_key},
            )
        else:
            self._cypher(
                """
                MATCH (d:DocumentChunk {namespace: $namespace, doc_name: $doc_name})
                DETACH DELETE d
                """,
                {"namespace": ns, "doc_name": doc_name},
            )
        self._cypher(
            """
            MATCH (e:Entity {namespace: $namespace})
            WHERE NOT ( (:DocumentChunk {namespace: $namespace})-[:MENTION]->(e) )
            DETACH DELETE e
            """,
            {"namespace": ns},
        )

    def _extract_payload(self, text: str) -> ExtractedGraphPayload:
        schema = self._cfg.schema
        mode = (self._cfg.extraction_mode or "llm").lower()
        if mode == "rule":
            return self._rule_extractor.extract(text, schema=schema)
        try:
            return self._llm_extractor.extract(text, schema=schema)
        except Exception as exc:  # noqa: BLE001
            if self._cfg.extraction_fallback_rule:
                logger.warning("LLM extraction failed, fallback to rule: %s", exc)
                return self._rule_extractor.extract(text, schema=schema)
            logger.warning("LLM extraction failed (no fallback): %s", exc)
            return ExtractedGraphPayload()

    def _cypher(self, query: str, params: dict[str, Any]) -> None:
        self._client.execute_cypher(query, params)

    @staticmethod
    def _build_doc_key(namespace: str, doc_name: str, doc_version: str) -> str:
        return f"{namespace}::{doc_name}::{doc_version}"

    def _upsert_chunk(
        self,
        dataset_id: str,
        namespace: str,
        chunk_id: str,
        text: str,
        doc_name: str,
        doc_version: str,
        doc_key: str,
    ) -> None:
        self._cypher(
            """
            MERGE (d:DocumentChunk {chunk_id: $chunk_id, namespace: $namespace})
            SET d.dataset_id = $dataset_id,
                d.doc_name = $doc_name,
                d.doc_version = $doc_version,
                d.doc_key = $doc_key,
                d.text = $text,
                d.updated_at = datetime()
            """,
            {
                "chunk_id": chunk_id,
                "namespace": namespace,
                "dataset_id": dataset_id,
                "doc_name": doc_name,
                "doc_version": doc_version,
                "doc_key": doc_key,
                "text": text,
            },
        )

    def _upsert_entity(self, namespace: str, ent: ExtractedEntity) -> None:
        self._cypher(
            """
            MERGE (e:Entity {entity_id: $entity_id, namespace: $namespace})
            SET e.name = $name,
                e.type = $type,
                e.updated_at = datetime()
            """,
            {
                "entity_id": ent.id,
                "namespace": namespace,
                "name": ent.name,
                "type": ent.type,
            },
        )

    def _link_chunk_entity(self, ns: str, chunk_id: str, entity_id: str) -> None:
        self._cypher(
            """
            MATCH (d:DocumentChunk {chunk_id: $chunk_id, namespace: $namespace})
            MATCH (e:Entity {entity_id: $entity_id, namespace: $namespace})
            MERGE (d)-[:MENTION]->(e)
            """,
            {"chunk_id": chunk_id, "entity_id": entity_id, "namespace": ns},
        )

    def _link_entity_relation(self, ns: str, rel: ExtractedRelation) -> None:
        self._cypher(
            """
            MATCH (a:Entity {entity_id: $source_id, namespace: $namespace})
            MATCH (b:Entity {entity_id: $target_id, namespace: $namespace})
            MERGE (a)-[r:GRAPH_REL {rel_type: $rel_type}]->(b)
            SET r.updated_at = datetime(),
                r.source = coalesce(r.source, 'llm')
            """,
            {
                "source_id": rel.source_id,
                "target_id": rel.target_id,
                "namespace": ns,
                "rel_type": rel.type,
            },
        )

    def _link_cooccur(self, ns: str, e1: str, e2: str) -> None:
        self._cypher(
            """
            MATCH (a:Entity {entity_id: $e1, namespace: $namespace})
            MATCH (b:Entity {entity_id: $e2, namespace: $namespace})
            MERGE (a)-[r:CO_OCCUR]->(b)
            SET r.weight = coalesce(r.weight, 0) + 1,
                r.updated_at = datetime()
            """,
            {"e1": e1, "e2": e2, "namespace": ns},
        )
