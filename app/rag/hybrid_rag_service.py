from __future__ import annotations

"""
HybridRAGService

在通用 RAGService（已支持向量+关键词混合检索）的基础上，引入 GraphRAG 能力：
- 根据配置选择仅检索库（vector 模式）、仅图检索（graph 模式）或检索库 + 图混合（hybrid 模式）；
- hybrid 模式下，负责将检索库结果与图事实进行融合。
"""

from typing import List, Sequence

from app.core.config import GraphRAGConfig, RAGConfig, get_app_config
from app.core.logging import get_logger
from app.graph.query_service import GraphQueryService
from app.rag.rag_service import RAGService
from app.rag.retrieval_policy import RetrievalPolicy

logger = get_logger(__name__)


class HybridRAGService:
    """
    基于配置的 Hybrid RAG 服务。

    默认行为：
    - 若 AppConfig.rag.graph.enabled 为 False，则完全等价于 RAGService（即检索库链路）。
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
        self._policy = RetrievalPolicy(self._graph_cfg)

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
            # GraphRAG 未启用，回退到检索库链路（由 RAGService 决定具体检索策略）
            return self._rag_service.retrieve_context(query, top_k=top_k, namespace=namespace)

        routed = self._policy.decide(query)
        mode = routed.mode

        if mode == "graph":
            return self._retrieve_graph_only(
                query=query,
                namespace=namespace,
                graph_hops=routed.graph_hops,
                max_graph_items=routed.max_graph_items,
            )
        if mode == "hybrid":
            return self._retrieve_hybrid(
                query=query,
                top_k=top_k,
                namespace=namespace,
                vector_weight=routed.vector_weight,
                graph_weight=routed.graph_weight,
                graph_hops=routed.graph_hops,
                max_graph_items=routed.max_graph_items,
            )
        # 默认或未知值时，回退到检索库链路
        return self._rag_service.retrieve_context(query, top_k=top_k, namespace=namespace)

    # Internal helpers -------------------------------------------------------

    def _retrieve_graph_only(
        self,
        query: str,
        namespace: str | None,
        graph_hops: int,
        max_graph_items: int,
    ) -> List[str]:
        facts = self._graph_query.query_relevant_facts(  # type: ignore[union-attr]
            question=query,
            namespace=namespace,
            max_hops=graph_hops,
            max_items=max_graph_items,
        )
        return facts

    def _retrieve_hybrid(
        self,
        query: str,
        top_k: int | None,
        namespace: str | None,
        vector_weight: float,
        graph_weight: float,
        graph_hops: int,
        max_graph_items: int,
    ) -> List[str]:
        """
        简单混合策略：
        - 先用检索库链路得到文本片段；
        - 再用问题在图中查询相关事实；
        - 按 vector_weight / graph_weight 比例拼接，并截断到 max_context_items。
        """
        vec_ctx = self._rag_service.retrieve_context(query, top_k=top_k, namespace=namespace)
        facts = self._graph_query.query_relevant_facts(  # type: ignore[union-attr]
            question=query,
            namespace=namespace,
            max_hops=graph_hops,
            max_items=max_graph_items,
        )

        max_items = self._graph_cfg.strategy.max_context_items
        vw = max(vector_weight, 0.0)
        gw = max(graph_weight, 0.0)
        total_weight = vw + gw or 1.0
        vw /= total_weight
        gw /= total_weight

        # 按权重决定保留条目数，且在两侧均有结果时至少各保留 1 条，避免单侧完全饥饿。
        vec_limit = int(round(max_items * vw))
        graph_limit = max_items - vec_limit
        if vec_ctx and facts and max_items >= 2:
            vec_limit = max(1, vec_limit)
            graph_limit = max(1, graph_limit)
            if vec_limit + graph_limit > max_items:
                if vec_limit >= graph_limit:
                    vec_limit -= 1
                else:
                    graph_limit -= 1

        vec_part = vec_ctx[:vec_limit] if vec_limit > 0 else []
        graph_part = facts[:graph_limit] if graph_limit > 0 else []
        return self._interleave_merge(vec_part, graph_part, max_items=max_items)

    # RetrievalPolicy 作为统一策略层，负责 query -> decision 路由。

    @staticmethod
    def _interleave_merge(vec_part: Sequence[str], graph_part: Sequence[str], max_items: int) -> List[str]:
        combined: List[str] = []
        i, j = 0, 0
        while len(combined) < max_items and (i < len(vec_part) or j < len(graph_part)):
            if i < len(vec_part):
                combined.append(vec_part[i])
                i += 1
                if len(combined) >= max_items:
                    break
            if j < len(graph_part):
                combined.append(graph_part[j])
                j += 1
        return combined[:max_items]

