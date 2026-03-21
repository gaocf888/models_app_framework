from __future__ import annotations

"""
GraphQueryService

负责在检索阶段从图数据库中查询与问题/实体相关的子图或事实，用于 GraphRAG 或 Hybrid RAG。

当前版本：
- 仅初始化 Neo4jGraph 连接与接口骨架；
- 查询策略（根据问题抽取实体、构造 Cypher 等）留作后续扩展。
"""

from typing import List, Optional

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

        当前实现仅返回空列表，预留后续扩展：
        - 基于 LLM/NLP 从 question 中抽取候选实体；
        - 结合 GraphSchemaConfig 构造 Cypher 查询，取邻域子图；
        - 将节点/关系转为自然语言事实句子。
        """
        if not self._cfg.enabled or self._graph is None:
            return []

        # TODO: 实现 question → entities → Cypher → facts 的完整链路。
        logger.debug(
            "GraphQueryService.query_relevant_facts called (namespace=%s, max_hops=%s, max_items=%s)",
            namespace,
            max_hops,
            max_items,
        )
        return []

