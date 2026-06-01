import unittest
from unittest.mock import MagicMock, patch

from app.core.config import GraphRAGConfig, RAGConfig
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
        self.delete_calls = []

    def delete_document(self, **kwargs):
        self.delete_calls.append(kwargs)


class TestGraphDeleteOnRag(unittest.TestCase):
    @patch("app.rag.ingestion.get_app_config")
    @patch("app.rag.ingestion.GraphIngestionService")
    def test_delete_on_rag_initializes_graph_without_ingest(self, mock_graph_cls, mock_cfg):
        graph = _FakeGraphIngestion()
        mock_graph_cls.return_value = graph
        rag_cfg = RAGConfig()
        rag_cfg.graph = GraphRAGConfig(
            enabled=True,
            ingest_on_rag=False,
            delete_on_rag=True,
            uri="bolt://x",
            username="u",
            password="p",
        )
        mock_cfg.return_value.rag = rag_cfg

        svc = RAGIngestionService(
            embedding_service=_FakeEmbeddingService(),
            store_provider=_FakeStoreProvider(),
        )
        svc.delete_by_doc_name("doc_a", namespace="ns1")
        mock_graph_cls.assert_called_once()
        self.assertEqual(1, len(graph.delete_calls))

    @patch("app.rag.ingestion.get_app_config")
    @patch("app.rag.ingestion.GraphIngestionService")
    def test_delete_on_rag_false_skips_graph(self, mock_graph_cls, mock_cfg):
        rag_cfg = RAGConfig()
        rag_cfg.graph = GraphRAGConfig(
            enabled=True,
            ingest_on_rag=False,
            delete_on_rag=False,
        )
        mock_cfg.return_value.rag = rag_cfg

        svc = RAGIngestionService(
            embedding_service=_FakeEmbeddingService(),
            store_provider=_FakeStoreProvider(),
        )
        svc.delete_by_doc_name("doc_a", namespace="ns1")
        mock_graph_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
