from __future__ import annotations

"""
Neo4j 连接懒加载封装。
"""

from typing import Any

from app.core.config import GraphRAGConfig
from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    from langchain_community.graphs import Neo4jGraph  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    Neo4jGraph = None  # type: ignore[assignment]


class Neo4jGraphClient:
    """按配置懒加载 Neo4jGraph 实例。"""

    def __init__(self, cfg: GraphRAGConfig) -> None:
        self._cfg = cfg
        self._graph: Any | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.enabled)

    @property
    def graph(self) -> Any:
        if not self._cfg.enabled:
            raise RuntimeError("GraphRAG is disabled")
        if self._graph is not None:
            return self._graph
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
        logger.info("Neo4jGraphClient connected (uri=%s).", self._cfg.uri)
        return self._graph

    def run_cypher(self, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        g = self.graph
        if hasattr(g, "query"):
            rows = g.query(query, params=params)
            return list(rows or [])
        result = g.run(query, params)
        if hasattr(result, "data"):
            return list(result.data() or [])
        return []

    def execute_cypher(self, query: str, params: dict[str, Any]) -> None:
        self.run_cypher(query, params)

    def ping(self) -> dict[str, Any]:
        if not self._cfg.enabled:
            return {"ok": False, "reason": "GraphRAG disabled"}
        try:
            rows = self.run_cypher("RETURN 1 AS ok", {})
            return {"ok": bool(rows), "detail": rows[:1]}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": str(exc)}
