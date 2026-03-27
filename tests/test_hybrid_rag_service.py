import unittest

from app.core.config import GraphHybridStrategyConfig, GraphRAGConfig, RAGConfig
from app.rag.hybrid_rag_service import HybridRAGService


class _FakeRAGService:
    def retrieve_context(self, query: str, top_k: int | None = None, namespace: str | None = None):
        return [f"vec-{i}" for i in range(1, 10)]


class _FakeGraphQuery:
    def query_relevant_facts(self, question: str, namespace: str | None = None, max_hops: int | None = None, max_items: int | None = None):
        return [f"graph-{i}" for i in range(1, 10)]


class TestHybridRAGService(unittest.TestCase):
    def test_hybrid_weighted_interleave(self):
        rag_cfg = RAGConfig()
        graph_cfg = GraphRAGConfig(
            enabled=True,
            strategy=GraphHybridStrategyConfig(
                mode="hybrid",
                vector_weight=0.5,
                graph_weight=0.5,
                max_context_items=6,
            ),
        )
        svc = HybridRAGService(
            rag_service=_FakeRAGService(), graph_query=_FakeGraphQuery(), rag_cfg=rag_cfg, graph_cfg=graph_cfg
        )
        out = svc.retrieve("测试问题")
        self.assertEqual(6, len(out))
        self.assertEqual(["vec-1", "graph-1", "vec-2", "graph-2", "vec-3", "graph-3"], out)

    def test_intent_routing_relation_query_boosts_graph(self):
        rag_cfg = RAGConfig()
        graph_cfg = GraphRAGConfig(
            enabled=True,
            strategy=GraphHybridStrategyConfig(
                mode="vector",
                vector_weight=0.8,
                graph_weight=0.2,
                max_context_items=5,
                use_intent_routing=True,
            ),
        )
        svc = HybridRAGService(
            rag_service=_FakeRAGService(), graph_query=_FakeGraphQuery(), rag_cfg=rag_cfg, graph_cfg=graph_cfg
        )
        out = svc.retrieve("A服务与B服务的依赖关系是什么")
        self.assertTrue(any(x.startswith("graph-") for x in out))

    def test_intent_routing_uses_configurable_keywords(self):
        rag_cfg = RAGConfig()
        graph_cfg = GraphRAGConfig(
            enabled=True,
            strategy=GraphHybridStrategyConfig(
                mode="vector",
                vector_weight=1.0,
                graph_weight=0.0,
                max_context_items=4,
                use_intent_routing=True,
                relation_keywords=["拓扑"],
                relation_keywords_en=[],
                routed_relation_graph_weight=0.8,
                routed_relation_vector_weight=0.2,
            ),
        )
        svc = HybridRAGService(
            rag_service=_FakeRAGService(), graph_query=_FakeGraphQuery(), rag_cfg=rag_cfg, graph_cfg=graph_cfg
        )
        out = svc.retrieve("请分析系统拓扑")
        self.assertTrue(any(x.startswith("graph-") for x in out))


if __name__ == "__main__":
    unittest.main()
