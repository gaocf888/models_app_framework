from __future__ import annotations

"""
HybridRAGService

在现有向量检索 RAGService 的基础上，引入 GraphRAG 能力：
- 根据配置选择仅向量（兼容当前行为）、仅图检索或向量 + 图混合检索；
- 混合模式下，负责将向量检索结果与图事实进行简单融合。
"""

from typing import List, Sequence

from app.core.config import GraphRAGConfig, RAGConfig, get_app_config
from app.core.logging import get_logger
from app.graph.query_service import GraphQueryService
from app.rag.rag_service import RAGService

logger = get_logger(__name__)


class HybridRAGService:
    """
    基于配置的 Hybrid RAG 服务。

    默认行为：
    - 若 AppConfig.rag.graph.enabled 为 False，则完全等价于 RAGService。
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        graph_query: GraphQueryService | None = None,
        rag_cfg: RAGConfig | None = None,
        graph_cfg: GraphRAGConfig | None = None,
    ) -> None:
        app_cfg = get_app_config()
        self._rag_cfg: RAGConfig = rag_cfg or app_cfg.rag  # type: ignore[attr-defined]
        self._graph_cfg: GraphRAGConfig = graph_cfg or self._rag_cfg.graph

        self._rag_service = rag_service or RAGService()
        # 若配置启用了 GraphRAG，则优先使用传入实例，否则尝试默认初始化
        if graph_query is not None:
            self._graph_query = graph_query
        elif self._graph_cfg.enabled:
            try:
                self._graph_query = GraphQueryService(self._graph_cfg)
            except Exception as e:
                logger.warning("failed to initialize GraphQueryService: %s", e, exc_info=True)
                self._graph_query = None
        else:
            self._graph_query = None

    # Public API -------------------------------------------------------------

    def index_texts(self, texts: Sequence[str]) -> None:
        """
        向量索引行为与 RAGService 保持一致。
        GraphRAG 摄入逻辑在 RAGIngestionService 中处理。
        """
        self._rag_service.index_texts(texts)

    def retrieve(self, query: str, top_k: int | None = None, namespace: str | None = None) -> List[str]:
        """
        根据配置执行向量 / 图 / 混合检索。
        """
        if not self._graph_cfg.enabled or self._graph_query is None:
            # GraphRAG 未启用，退化为纯向量 RAG，与当前行为保持一致
            return self._rag_service.retrieve_context(query, top_k=top_k, namespace=namespace)

        strategy = self._graph_cfg.strategy
        mode = (strategy.mode or "vector").lower()

        if mode == "graph":
            return self._retrieve_graph_only(query, namespace)
        if mode == "hybrid":
            return self._retrieve_hybrid(query, top_k, namespace)
        # 默认或未知值时，回退到纯向量
        return self._rag_service.retrieve_context(query, top_k=top_k, namespace=namespace)

    # Internal helpers -------------------------------------------------------

    def _retrieve_graph_only(self, query: str, namespace: str | None) -> List[str]:
        facts = self._graph_query.query_relevant_facts(  # type: ignore[union-attr]
            question=query,
            namespace=namespace,
            max_hops=self._graph_cfg.strategy.graph_hops,
            max_items=self._graph_cfg.strategy.max_graph_items,
        )
        return facts

    def _retrieve_hybrid(self, query: str, top_k: int | None, namespace: str | None) -> List[str]:
        """
        简单混合策略：
        - 先用向量检索得到文本片段；
        - 再用问题在图中查询相关事实；
        - 按 vector_weight / graph_weight 比例拼接，并截断到 max_context_items。
        """
        vec_ctx = self._rag_service.retrieve_context(query, top_k=top_k, namespace=namespace)
        facts = self._graph_query.query_relevant_facts(  # type: ignore[union-attr]
            question=query,
            namespace=namespace,
            max_hops=self._graph_cfg.strategy.graph_hops,
            max_items=self._graph_cfg.strategy.max_graph_items,
        )

        max_items = self._graph_cfg.strategy.max_context_items
        vw = max(self._graph_cfg.strategy.vector_weight, 0.0)
        gw = max(self._graph_cfg.strategy.graph_weight, 0.0)
        total_weight = vw + gw or 1.0
        vw /= total_weight
        gw /= total_weight

        # 按权重决定优先保留的条目数
        vec_limit = int(max_items * vw)
        graph_limit = max_items - vec_limit

        vec_part = vec_ctx[:vec_limit] if vec_limit > 0 else []
        graph_part = facts[:graph_limit] if graph_limit > 0 else []

        combined: list[str] = []
        combined.extend(vec_part)
        combined.extend(graph_part)
        return combined[:max_items]

