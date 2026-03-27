import unittest

from app.core.config import GraphHybridStrategyConfig, GraphRAGConfig
from app.rag.retrieval_policy import RetrievalPolicy


class TestRetrievalPolicy(unittest.TestCase):
    def test_relation_query_routes_to_hybrid(self):
        cfg = GraphRAGConfig(
            enabled=True,
            strategy=GraphHybridStrategyConfig(
                mode="vector",
                use_intent_routing=True,
                relation_keywords=["依赖"],
                relation_keywords_en=[],
                routed_relation_graph_weight=0.9,
                routed_relation_vector_weight=0.1,
                routed_relation_graph_hops=3,
            ),
        )
        decision = RetrievalPolicy(cfg).decide("服务A对服务B有依赖关系吗")
        self.assertEqual("hybrid", decision.mode)
        self.assertGreaterEqual(decision.graph_weight, 0.9)
        self.assertGreaterEqual(decision.graph_hops, 3)

    def test_definition_query_prefers_vector_under_hybrid(self):
        cfg = GraphRAGConfig(
            enabled=True,
            strategy=GraphHybridStrategyConfig(
                mode="hybrid",
                use_intent_routing=True,
                definition_keywords=["定义"],
                definition_keywords_en=[],
                routed_definition_vector_weight=0.85,
                routed_definition_graph_weight=0.15,
            ),
        )
        decision = RetrievalPolicy(cfg).decide("请给出这个模块的定义")
        self.assertGreaterEqual(decision.vector_weight, 0.85)
        self.assertLessEqual(decision.graph_weight, 0.15)


if __name__ == "__main__":
    unittest.main()
