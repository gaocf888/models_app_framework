import unittest
from unittest.mock import MagicMock, patch

from app.graph.admin_service import GraphAdminService


class TestGraphAdminRebuild(unittest.TestCase):
    @patch("app.graph.admin_service.GraphIngestionService")
    @patch("app.graph.admin_service.VectorStoreProvider")
    @patch("app.graph.admin_service.DocumentRepository")
    def test_full_rebuild_uses_document_repository(self, mock_doc_repo_cls, mock_store_cls, mock_ing_cls):
        from app.core.config import GraphRAGConfig

        cfg = GraphRAGConfig(enabled=True, uri="bolt://x", username="u", password="p")
        svc = GraphAdminService(cfg)

        doc_repo = MagicMock()
        doc_repo.list.return_value = [
            {
                "doc_name": "doc_a",
                "namespace": "ns1",
                "doc_version": "v2",
                "dataset_id": "ds_a",
            }
        ]
        mock_doc_repo_cls.return_value = doc_repo

        store = MagicMock()
        store.list_chunk_texts_for_document.return_value = ["chunk1", "chunk2"]
        mock_store_cls.return_value.get_default_store.return_value = store

        ingestion = MagicMock()
        mock_ing_cls.return_value = ingestion

        result = svc.rebuild(mode="full", namespace="ns1")

        self.assertEqual(1, result["rebuilt_docs"])
        self.assertEqual(2, result["rebuilt_chunks"])
        ingestion.ingest_from_chunks.assert_called_once()
        call_kw = ingestion.ingest_from_chunks.call_args[1]
        self.assertEqual("ds_a", call_kw["dataset_id"])
        self.assertEqual("v2", call_kw["doc_version"])


if __name__ == "__main__":
    unittest.main()
