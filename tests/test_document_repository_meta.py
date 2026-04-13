import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.rag.document_repository import DocumentRepository, make_document_storage_key
from app.rag.vector_store import InMemoryVectorStore


def _mock_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.rag.vector_store_type = "inmemory"
    cfg.rag.es.docs_index_name = "docs"
    cfg.rag.es.docs_index_version = "1"
    cfg.rag.es.docs_index_alias = "docs_alias"
    cfg.rag.ingestion.tenant_id_default = "__tenant__"
    return cfg


class TestVectorStoreListChunks(unittest.TestCase):
    def test_list_chunk_texts_sorts_by_chunk_index(self) -> None:
        store = InMemoryVectorStore()
        store.add_texts(
            ["third", "first", "second"],
            [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
            namespace="ns",
            doc_name="doc",
            metadatas=[
                {"doc_version": "v1", "chunk_index": 2},
                {"doc_version": "v1", "chunk_index": 0},
                {"doc_version": "v1", "chunk_index": 1},
            ],
        )
        texts = store.list_chunk_texts_for_document("doc", "ns", "v1")
        self.assertEqual(texts, ["first", "second", "third"])


class TestDocumentRepositoryNamespaceMove(unittest.TestCase):
    def test_make_storage_key(self) -> None:
        k = make_document_storage_key(
            "n1",
            namespace=None,
            tenant_id=None,
            doc_version=None,
            tenant_id_fallback="__tenant__",
        )
        self.assertEqual(k, "__tenant__::__default__::n1::v1")

    @patch("app.rag.document_repository.get_app_config")
    def test_move_document_to_namespace_changes_key(self, gc: MagicMock) -> None:
        gc.return_value = _mock_cfg()
        with tempfile.TemporaryDirectory() as d:
            repo = DocumentRepository(state_dir=d)
            old_key = make_document_storage_key(
                "doc1",
                namespace="ns_a",
                tenant_id=None,
                doc_version="v1",
                tenant_id_fallback="__tenant__",
            )
            repo.upsert(
                old_key,
                {
                    "doc_name": "doc1",
                    "doc_version": "v1",
                    "tenant_id": None,
                    "dataset_id": "ds",
                    "namespace": "ns_a",
                    "source_type": "text",
                    "chunk_count": 2,
                    "metadata": {},
                    "status": "ready",
                    "created_at": "t0",
                    "updated_at": "t0",
                },
            )
            out = repo.move_document_to_namespace(
                "doc1",
                from_namespace="ns_a",
                to_namespace="ns_b",
            )
            self.assertEqual(out["namespace"], "ns_b")
            new_key = make_document_storage_key(
                "doc1",
                namespace="ns_b",
                tenant_id=None,
                doc_version="v1",
                tenant_id_fallback="__tenant__",
            )
            self.assertIsNotNone(repo.get(new_key))
            self.assertIsNone(repo.get(old_key))

    @patch("app.rag.document_repository.get_app_config")
    def test_move_document_to_namespace_ambiguous(self, gc: MagicMock) -> None:
        gc.return_value = _mock_cfg()
        with tempfile.TemporaryDirectory() as d:
            repo = DocumentRepository(state_dir=d)
            for ds in ("ds1", "ds2"):
                # 存储主键任意唯一即可；匹配逻辑看 payload
                key = f"row_{ds}"
                # 同一默认 namespace、同名同版本，但不同 dataset —— 不传 dataset_id 过滤时会命中 2 条
                repo.upsert(
                    key,
                    {
                        "doc_name": "same",
                        "doc_version": "v1",
                        "dataset_id": ds,
                        "namespace": None,
                        "source_type": "text",
                        "chunk_count": 0,
                        "metadata": {},
                        "status": "ready",
                        "created_at": "t",
                        "updated_at": "t",
                    },
                )
            with self.assertRaises(ValueError):
                repo.move_document_to_namespace(
                    "same",
                    from_namespace=None,
                    to_namespace="z",
                )


if __name__ == "__main__":
    unittest.main()
