from __future__ import annotations

"""
GraphIngestionService

负责在 RAG 摄入阶段，将文本分片转换为图结构（实体 + 关系），并写入图数据库。

当前版本：
- 仅初始化 Neo4jGraph 连接与接口骨架；
- 实体/关系抽取策略留作后续扩展（可结合 LLM / 规则等实现）。
"""

from dataclasses import dataclass
from typing import List, Optional

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
    ) -> None:
        """
        从一批文本分片中抽取实体与关系并写入图数据库。

        当前实现仅作为骨架：
        - 记录日志，预留抽取 + 写入调用点；
        - 具体抽取逻辑与 Cypher 写入将在后续迭代中补充。
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

        # TODO: 实现实体/关系抽取与写入逻辑：
        # 1. 调用抽取器（LLM/规则），返回 ExtractedEntity / ExtractedRelation 列表；
        # 2. 根据 self._cfg.schema（若启用）进行类型映射与字段校验；
        # 3. 使用 self._graph.run(...) 执行 Cypher，将节点与关系写入 Neo4j。

