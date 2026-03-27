from __future__ import annotations

"""
GraphQueryService

负责在检索阶段从图数据库中查询与问题/实体相关的子图或事实，用于 GraphRAG 或 Hybrid RAG。

当前版本：
- 仅初始化 Neo4jGraph 连接与接口骨架；
- 查询策略（根据问题抽取实体、构造 Cypher 等）留作后续扩展。
"""

import re
from typing import Any, List, Optional

from app.core.config import GraphRAGConfig, get_app_config
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from langchain_community.graphs import Neo4jGraph  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    Neo4jGraph = None  # type: ignore[assignment]


class GraphQueryService:
    """
    GraphRAG 查询服务：对 Neo4j 执行图查询并返回结构化结果。
    """

    def __init__(self, cfg: GraphRAGConfig | None = None) -> None:
        app_cfg = get_app_config()
        self._cfg = cfg or app_cfg.rag.graph  # type: ignore[attr-defined]

        if not self._cfg.enabled:
            self._graph = None
            logger.info("GraphQueryService initialized but GraphRAG is disabled.")
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
        logger.info("GraphQueryService initialized with Neo4jGraph (uri=%s).", self._cfg.uri)

    # Public API -------------------------------------------------------------

    def query_relevant_facts(
        self,
        question: str,
        namespace: Optional[str] = None,
        max_hops: Optional[int] = None,
        max_items: Optional[int] = None,
    ) -> List[str]:
        """
        查询与问题相关的图事实，返回适合拼接到 RAG 上下文中的文本列表。

        企业可用轻量实现：
        - 规则抽取 query 实体；
        - 查询 Entity 与 DocumentChunk / CO_OCCUR 邻域；
        - 返回可拼接到 RAG 上下文的事实文本。
        """
        if not self._cfg.enabled or self._graph is None:
            return []
        ns = namespace or "__default__"
        hops = max_hops or self._cfg.strategy.graph_hops
        limit = max_items or self._cfg.strategy.max_graph_items
        terms = self._extract_terms(question)
        if not terms:
            return []

        facts: List[str] = []
        rows = self._cypher_rows(
            """
            MATCH (e:Entity {namespace: $namespace})<-[:MENTION]-(d:DocumentChunk {namespace: $namespace})
            WHERE e.entity_id IN $terms OR toLower(e.name) IN $terms
            RETURN e.name AS entity, d.text AS text
            LIMIT $limit
            """,
            {"namespace": ns, "terms": terms, "limit": limit},
        )
        for r in rows:
            entity = r.get("entity")
            text = r.get("text")
            if entity and text:
                facts.append(
                    self._cfg.fact_template_entity.format(
                        entity=entity,
                        text=text,
                    )
                )

        if hops >= 2 and len(facts) < limit:
            co_rows = self._cypher_rows(
                """
                MATCH (a:Entity {namespace: $namespace})-[r:CO_OCCUR]->(b:Entity {namespace: $namespace})
                WHERE a.entity_id IN $terms OR toLower(a.name) IN $terms
                RETURN a.name AS a_name, b.name AS b_name, r.weight AS weight
                ORDER BY weight DESC
                LIMIT $limit
                """,
                {"namespace": ns, "terms": terms, "limit": limit},
            )
            for r in co_rows:
                a_name = r.get("a_name")
                b_name = r.get("b_name")
                w = r.get("weight")
                if a_name and b_name:
                    weight = int(w or 0)
                    if weight < max(1, self._cfg.min_cooccur_weight):
                        continue
                    facts.append(
                        self._cfg.fact_template_cooccur.format(
                            a=a_name,
                            b=b_name,
                            weight=weight,
                        )
                    )

        seen = set()
        unique: List[str] = []
        for f in facts:
            if f in seen:
                continue
            seen.add(f)
            unique.append(f)
            if len(unique) >= limit:
                break
        return unique

    @staticmethod
    def _extract_terms(question: str) -> List[str]:
        cfg = get_app_config().rag.graph
        min_len = max(1, cfg.entity_min_len)
        max_len = max(min_len, cfg.entity_max_len)
        zh_max = max(min_len, min(max_len, cfg.zh_entity_max_len))
        en_min = max(1, cfg.en_entity_min_len)
        en_max = max(en_min, min(max_len, cfg.en_entity_max_len))
        zh_terms = re.findall(rf"[\u4e00-\u9fff]{{{min_len},{zh_max}}}", question or "")
        en_terms = re.findall(rf"\b[A-Z][a-zA-Z0-9]{{{en_min - 1},{en_max}}}\b", question or "")
        vals = [t.strip().lower() for t in (zh_terms + en_terms) if t.strip()]
        return list(dict.fromkeys(vals))

    def _cypher_rows(self, query: str, params: dict[str, Any]) -> List[dict[str, Any]]:
        if hasattr(self._graph, "query"):
            rows = self._graph.query(query, params=params)  # type: ignore[union-attr]
        else:
            rows = self._graph.run(query, params)  # type: ignore[union-attr]
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
        return []

