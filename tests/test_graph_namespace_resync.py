import unittest
from unittest.mock import MagicMock, patch

from app.rag.graph_namespace_resync import run_graph_resync_after_namespace_move


class TestGraphNamespaceResync(unittest.TestCase):
    @patch("app.rag.graph_namespace_resync.RAGIngestionService")
    @patch("app.rag.graph_namespace_resync.get_app_config")
    def test_resync_deletes_old_and_ingests_new(self, mock_cfg, mock_ing_cls) -> None:
        mock_cfg.return_value.rag.graph.enabled = True

        graph = MagicMock()
        store = MagicMock()
        store.list_chunk_texts_for_document.return_value = ["chunk_a", "chunk_b"]

        ing = MagicMock()
        ing._graph_ingestion = graph
        ing._rag_service._store_provider.get_default_store.return_value = store
        mock_ing_cls.return_value = ing

        run_graph_resync_after_namespace_move(
            doc_name="mydoc",
            from_namespace="old_ns",
            to_namespace="new_ns",
            doc_version="v2",
            dataset_id="ds1",
        )

        store.list_chunk_texts_for_document.assert_called_once_with(
            doc_name="mydoc",
            namespace="new_ns",
            doc_version="v2",
        )
        graph.delete_document.assert_called_once_with(
            doc_name="mydoc",
            namespace="old_ns",
            doc_version="v2",
        )
        graph.ingest_from_chunks.assert_called_once()
        call_kw = graph.ingest_from_chunks.call_args[1]
        self.assertEqual(call_kw["dataset_id"], "ds1")
        self.assertEqual(call_kw["texts"], ["chunk_a", "chunk_b"])
        self.assertEqual(call_kw["namespace"], "new_ns")
        self.assertEqual(call_kw["doc_name"], "mydoc")
        self.assertEqual(call_kw["doc_version"], "v2")

    @patch("app.rag.graph_namespace_resync.get_app_config")
    def test_skips_when_graph_disabled(self, mock_cfg) -> None:
        mock_cfg.return_value.rag.graph.enabled = False
        with patch("app.rag.graph_namespace_resync.RAGIngestionService") as mock_ing_cls:
            run_graph_resync_after_namespace_move(
                doc_name="x",
                from_namespace="a",
                to_namespace="b",
                doc_version=None,
                dataset_id="ds",
            )
        mock_ing_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
