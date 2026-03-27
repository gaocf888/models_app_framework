import unittest

from app.rag.rag_service import RAGService


class _FakeEmbeddingService:
    def embed_text(self, query):
        return [0.1, 0.2]


class _FakeStore:
    def similarity_search_by_vector(self, vector, k=5, namespace=None):
        return []

    def keyword_search(self, query, k=5, namespace=None):
        return []

    def metadata_search(self, query, k=5, namespace=None):
        return [
            {
                "text": "metadata matched chunk",
                "score": 1.0,
                "ext_id": "m1",
                "namespace": namespace,
                "doc_name": "doc_meta",
                "metadata": {"doc_version": "v2", "tenant_id": "t1"},
            }
        ]

    def add_texts(self, *args, **kwargs):
        return []

    def delete_by_doc_name(self, doc_name, namespace=None):
        return 0


class _FakeStoreProvider:
    def __init__(self):
        self._store = _FakeStore()

    def get_default_store(self):
        return self._store


class TestMetadataRecall(unittest.TestCase):
    def test_metadata_recall_channel_can_return_chunks(self):
        svc = RAGService(
            embedding_service=_FakeEmbeddingService(),
            store_provider=_FakeStoreProvider(),
        )
        svc._cfg.hybrid.metadata_recall_enabled = True
        svc._rerank = lambda query, hits: hits
        chunks = svc.retrieve_chunks("doc_version v2", top_k=1, namespace="ns1", use_hybrid=True)
        self.assertEqual(1, len(chunks))
        self.assertEqual("doc_meta", chunks[0].doc_name)
        self.assertEqual("v2", chunks[0].metadata.get("doc_version"))

    def test_inmemory_metadata_search_supports_chinese_tokens(self):
        from app.rag.vector_store import InMemoryVectorStore

        store = InMemoryVectorStore()
        store.add_texts(
            texts=["正文内容"],
            embeddings=[[0.1, 0.2]],
            namespace="ns1",
            doc_name="设备台账文档",
            metadatas=[{"tenant_id": "租户A", "doc_version": "v2"}],
        )
        hits = store.metadata_search("查询设备台账", k=3, namespace="ns1")
        self.assertTrue(len(hits) >= 1)


if __name__ == "__main__":
    unittest.main()
