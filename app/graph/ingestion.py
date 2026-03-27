from __future__ import annotations

"""
GraphIngestionService

负责在 RAG 摄入阶段，将文本分片转换为图结构（实体 + 关系），并写入图数据库。

当前版本：
- 仅初始化 Neo4jGraph 连接与接口骨架；
- 实体/关系抽取策略留作后续扩展（可结合 LLM / 规则等实现）。
"""

from dataclasses import dataclass
import re
from typing import Any, List, Optional

from app.core.config import GraphRAGConfig, get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    # LangChain Graph 封装（可选依赖，实际使用前需在环境中安装）
    from langchain_community.graphs import Neo4jGraph  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - 可选依赖
    Neo4jGraph = None  # type: ignore[assignment]


@dataclass
class ExtractedEntity:
    type: str
    id: str | None
    name: str | None
    properties: dict


@dataclass
class ExtractedRelation:
    type: str
    source_id: str
    target_id: str
    properties: dict


class GraphIngestionService:
    """
    GraphRAG 摄入服务：将文本分片转换为图结构并写入 Neo4j。

    说明：
    - 具体的实体/关系抽取逻辑目前预留接口，后续可接入 LLM 抽取链或规则；
    - Schema 映射行为由 GraphRAGConfig.schema 控制，未启用 Schema 时采用宽松模式。
    """

    def __init__(self, cfg: GraphRAGConfig | None = None) -> None:
        app_cfg = get_app_config()
        self._cfg = cfg or app_cfg.rag.graph  # type: ignore[attr-defined]

        if not self._cfg.enabled:
            self._graph = None
            logger.info("GraphIngestionService initialized but GraphRAG is disabled.")
            return

        if Neo4jGraph is None:
            raise ImportError(
                "GraphRAG enabled but langchain-community[neo4j] is not installed. "
                "Install dependencies from requirements-大模型应用.txt."
            )

        if not self._cfg.uri or not self._cfg.username or not self._cfg.password:
            raise ValueError(
                "GraphRAG enabled but Neo4j connection info is incomplete. "
                "Please configure NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD."
            )

        self._graph = Neo4jGraph(
            url=self._cfg.uri,
            username=self._cfg.username,
            password=self._cfg.password,
            database=self._cfg.database,
        )
        logger.info("GraphIngestionService initialized with Neo4jGraph (uri=%s).", self._cfg.uri)

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
        """
        从一批文本分片中抽取实体与关系并写入图数据库。

        企业可用轻量实现：
        - 基于规则抽取候选实体（中英混合）；
        - 将 chunk 作为 Document 节点，将实体作为 Entity 节点；
        - 写入 MENTION / CO_OCCUR 关系，支持后续图检索召回事实。
        """
        if not self._cfg.enabled or self._graph is None:
            # GraphRAG 未启用，直接返回
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
            # 与向量侧“同名文档先删后灌”保持一致，避免旧 chunk 残留导致图检索漂移。
            self.delete_document(doc_name=effective_doc_name, namespace=ns, doc_version=doc_version)
        for idx, chunk_text in enumerate(texts):
            entities = self._extract_entities(chunk_text)
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
            if not entities:
                continue
            for ent in entities:
                self._upsert_entity(ns, ent)
                self._link_chunk_entity(ns=ns, chunk_id=chunk_id, entity_id=ent.id or "")
            # 在同一 chunk 中建立共现关系
            for i in range(len(entities)):
                for j in range(i + 1, len(entities)):
                    self._link_cooccur(ns, entities[i].id or "", entities[j].id or "")

    def delete_document(self, doc_name: str, namespace: Optional[str] = None, doc_version: str | None = None) -> None:
        """
        删除图侧文档相关节点与关系。

        - 指定 doc_version：删除指定版本；
        - 不指定 doc_version：按 doc_name 删除该 namespace 下所有版本。
        """
        if not self._cfg.enabled or self._graph is None:
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
        # 清理无入边实体，避免图持续膨胀。
        self._cypher(
            """
            MATCH (e:Entity {namespace: $namespace})
            WHERE NOT ( (:DocumentChunk {namespace: $namespace})-[:MENTION]->(e) )
            DETACH DELETE e
            """,
            {"namespace": ns},
        )

    def _extract_entities(self, text: str) -> List[ExtractedEntity]:
        # 规则：中英文实体候选（保守提取，避免噪声）。
        # 参数全部来自 GraphRAGConfig，便于线上按行业语料调整。
        min_len = max(1, self._cfg.entity_min_len)
        max_len = max(min_len, self._cfg.entity_max_len)
        zh_max = max(min_len, min(max_len, self._cfg.zh_entity_max_len))
        en_min = max(1, self._cfg.en_entity_min_len)
        en_max = max(en_min, min(max_len, self._cfg.en_entity_max_len))
        zh_terms = re.findall(rf"[\u4e00-\u9fff]{{{min_len},{zh_max}}}", text or "")
        en_terms = re.findall(rf"\b[A-Z][a-zA-Z0-9]{{{en_min - 1},{en_max}}}\b", text or "")
        seen: set[str] = set()
        out: List[ExtractedEntity] = []
        for term in zh_terms + en_terms:
            name = term.strip()
            if len(name) < min_len or len(name) > max_len:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(
                ExtractedEntity(
                    type="Concept",
                    id=key,
                    name=name,
                    properties={"name": name, "norm_name": key},
                )
            )
            if len(out) >= max(1, self._cfg.max_entities_per_chunk):
                break
        return out

    def _cypher(self, query: str, params: dict[str, Any]) -> None:
        # 兼容不同 Neo4jGraph 版本方法名
        if hasattr(self._graph, "query"):
            self._graph.query(query, params=params)  # type: ignore[union-attr]
            return
        self._graph.run(query, params)  # type: ignore[union-attr]

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

    def _link_cooccur(self, ns: str, e1: str, e2: str) -> None:
        if self._cfg.min_cooccur_weight > 1:
            # 预留：目前每次共现+1，权重阈值通过查询侧控制即可。
            # 这里保留参数注释，避免误以为未接入配置。
            pass
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

