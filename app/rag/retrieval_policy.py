from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

from app.core.config import GraphRAGConfig


@dataclass
class RetrievalDecision:
    mode: str
    vector_weight: float
    graph_weight: float
    graph_hops: int
    max_graph_items: int


class RetrievalPolicy:
    """
    统一检索策略层（轻量版）。

    当前职责：
    - 基于 GraphHybridStrategyConfig 计算检索模式和融合参数；
    - 在启用意图路由时，按配置化关键词调整图/向量侧权重与图查询预算。
    """

    def __init__(self, graph_cfg: GraphRAGConfig) -> None:
        self._graph_cfg = graph_cfg

    def decide(self, query: str) -> RetrievalDecision:
        strategy = self._graph_cfg.strategy
        mode = (strategy.mode or "vector").lower()
        decision = RetrievalDecision(
            mode=mode,
            vector_weight=max(strategy.vector_weight, 0.0),
            graph_weight=max(strategy.graph_weight, 0.0),
            graph_hops=max(1, strategy.graph_hops),
            max_graph_items=max(1, strategy.max_graph_items),
        )
        if not strategy.use_intent_routing:
            return decision

        q = (query or "").strip()
        ql = q.lower()
        relation_hit = self._contains_keywords(q, strategy.relation_keywords) or self._contains_keywords(
            ql, strategy.relation_keywords_en
        )
        definition_hit = self._contains_keywords(q, strategy.definition_keywords) or self._contains_keywords(
            ql, strategy.definition_keywords_en
        )

        if relation_hit and mode in {"vector", "hybrid"}:
            decision.mode = "hybrid"
            decision.graph_weight = max(decision.graph_weight, strategy.routed_relation_graph_weight)
            decision.vector_weight = min(decision.vector_weight, strategy.routed_relation_vector_weight)
            decision.graph_hops = max(decision.graph_hops, strategy.routed_relation_graph_hops)
            decision.max_graph_items = max(decision.max_graph_items, strategy.routed_relation_max_graph_items)
        elif definition_hit and mode == "hybrid":
            decision.vector_weight = max(decision.vector_weight, strategy.routed_definition_vector_weight)
            decision.graph_weight = min(decision.graph_weight, strategy.routed_definition_graph_weight)
        return decision

    @staticmethod
    def _contains_keywords(text: str, keywords: Sequence[str]) -> bool:
        if not text:
            return False
        for kw in keywords:
            kw_norm = (kw or "").strip()
            if not kw_norm:
                continue
            if re.search(re.escape(kw_norm), text):
                return True
        return False
