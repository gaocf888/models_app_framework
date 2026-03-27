import unittest

from app.nl2sql.rag_service import NL2SQLRAGService
from app.rag.models import RetrievedChunk


class _FakeRAG:
    def retrieve_chunks(self, query, top_k=None, namespace=None, scene=None):
        return [
            RetrievedChunk(
                text=f"vec:{namespace}",
                doc_name="doc_vec",
                namespace=namespace,
                chunk_id=f"vec:{namespace}",
                score=0.9,
            )
        ]


class _FakeGraphQuery:
    def query_relevant_facts(self, question, namespace=None, max_hops=None, max_items=None):
        return [f"[Graph] {namespace} fact"]


class _Decision:
    def __init__(self, mode: str):
        self.mode = mode
        self.vector_weight = 0.6
        self.graph_weight = 0.4
        self.graph_hops = 2
        self.max_graph_items = 5


class _Policy:
    def __init__(self, mode: str):
        self._mode = mode

    def decide(self, query):
        return _Decision(self._mode)


class TestNL2SQLRAGService(unittest.TestCase):
    def test_vector_mode_keeps_standard_vector_chunks(self):
        svc = NL2SQLRAGService(rag_service=_FakeRAG())
        svc._policy = _Policy("vector")
        svc._graph_query = _FakeGraphQuery()
        chunks = svc.retrieve_chunks("q", top_k=2)
        self.assertTrue(all(c.doc_name == "doc_vec" for c in chunks))
        self.assertEqual(3, len(chunks))

    def test_graph_mode_includes_graph_facts(self):
        svc = NL2SQLRAGService(rag_service=_FakeRAG())
        svc._policy = _Policy("graph")
        svc._graph_query = _FakeGraphQuery()
        chunks = svc.retrieve_chunks("q", top_k=2)
        self.assertTrue(all(c.metadata.get("source") == "graph" for c in chunks))
        self.assertEqual(3, len(chunks))


if __name__ == "__main__":
    unittest.main()
