import unittest

from app.rag.ingestion import RAGIngestionService
from app.rag.vector_store import InMemoryVectorStore


class _FakeEmbeddingService:
    def embed_texts(self, texts):
        return [[0.1, 0.2] for _ in texts]


class _FakeStoreProvider:
    def __init__(self):
        self.store = InMemoryVectorStore()

    def get_default_store(self):
        return self.store


class _FakeGraphIngestion:
    def __init__(self):
        self.ingest_calls = []
        self.delete_calls = []

    def ingest_from_chunks(self, **kwargs):
        self.ingest_calls.append(kwargs)

    def delete_document(self, **kwargs):
        self.delete_calls.append(kwargs)


class TestIngestionServiceHooks(unittest.TestCase):
    def test_post_index_hook_runs_graph_ingestion(self):
        graph = _FakeGraphIngestion()
        svc = RAGIngestionService(
            embedding_service=_FakeEmbeddingService(),
            store_provider=_FakeStoreProvider(),
            graph_ingestion=graph,
        )
        svc.ingest_texts(
            dataset_id="ds1",
            texts=["a", "b"],
            namespace="ns1",
            doc_name="doc_a",
            doc_version="v2",
            replace_if_exists=True,
        )
        self.assertEqual(1, len(graph.ingest_calls))
        call = graph.ingest_calls[0]
        self.assertEqual("doc_a", call.get("doc_name"))
        self.assertEqual("v2", call.get("doc_version"))
        self.assertTrue(call.get("replace_if_exists"))

    def test_delete_by_doc_name_calls_graph_cleanup(self):
        graph = _FakeGraphIngestion()
        svc = RAGIngestionService(
            embedding_service=_FakeEmbeddingService(),
            store_provider=_FakeStoreProvider(),
            graph_ingestion=graph,
        )
        svc.delete_by_doc_name("doc_a", namespace="ns1")
        self.assertEqual(1, len(graph.delete_calls))
        self.assertEqual("doc_a", graph.delete_calls[0].get("doc_name"))
        self.assertEqual("ns1", graph.delete_calls[0].get("namespace"))


if __name__ == "__main__":
    unittest.main()
