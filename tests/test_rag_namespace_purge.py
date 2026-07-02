import unittest

from app.rag.document_repository import DocumentRepository
from app.rag.ingestion import RAGIngestionService
from app.rag.models import DocumentSource, utcnow_iso
from app.rag.namespace_kb import merge_doc_metadata_for_record
from app.rag.vector_store import InMemoryVectorStore


class _FakeEmbeddingService:
    def embed_texts(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _FakeStoreProvider:
    def __init__(self, store):
        self._store = store

    def get_default_store(self):
        return self._store


class TestNamespacePurge(unittest.TestCase):
    def test_delete_by_namespace_clears_docs_and_chunks(self):
        store = InMemoryVectorStore()
        store.add_texts(
            texts=["a", "b"],
            embeddings=[[1.0, 0.0], [0.9, 0.1]],
            namespace="hr",
            doc_name="doc1",
            metadatas=[{"doc_version": "v1"}, {"doc_version": "v1"}],
        )
        store.add_texts(
            texts=["other"],
            embeddings=[[1.0, 0.0]],
            namespace="sales",
            doc_name="doc2",
            metadatas=[{"doc_version": "v1"}],
        )

        repo = DocumentRepository()
        repo._use_es = False  # noqa: SLF001
        repo._file_path.parent.mkdir(parents=True, exist_ok=True)
        doc = DocumentSource(
            dataset_id="ds",
            doc_name="doc1",
            namespace="hr",
            content="x",
        )
        repo.upsert(
            "default::hr::doc1::v1",
            {
                "doc_name": "doc1",
                "doc_version": "v1",
                "dataset_id": "ds",
                "namespace": "hr",
                "chunk_count": 2,
                "status": "SUCCESS",
                "created_at": utcnow_iso(),
                "updated_at": utcnow_iso(),
                "metadata": merge_doc_metadata_for_record(doc),
            },
        )

        svc = RAGIngestionService(
            embedding_service=_FakeEmbeddingService(),
            store_provider=_FakeStoreProvider(store),
            graph_ingestion=None,
        )
        result = svc.delete_by_namespace("hr")
        self.assertEqual(2, result["chunks_deleted"])
        self.assertEqual(1, result["doc_records_deleted"])
        self.assertEqual(1, result["documents_purged"])
        self.assertEqual(1, len(store.similarity_search_by_vector([1.0, 0.0], k=5, namespace="sales")))
        self.assertEqual(0, len(store.similarity_search_by_vector([1.0, 0.0], k=5, namespace="hr")))


if __name__ == "__main__":
    unittest.main()
