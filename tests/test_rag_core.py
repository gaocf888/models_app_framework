import unittest

from app.rag.rag_service import RAGService
from app.rag.vector_store import InMemoryVectorStore


class TestRAGCore(unittest.TestCase):
    def test_rrf_fusion_merges_two_channels(self):
        semantic_hits = [
            {"ext_id": "a", "text": "alpha"},
            {"ext_id": "b", "text": "beta"},
        ]
        keyword_hits = [
            {"ext_id": "b", "text": "beta"},
            {"ext_id": "c", "text": "gamma"},
        ]
        fused = RAGService._rrf_fuse(semantic_hits=semantic_hits, keyword_hits=keyword_hits, rrf_k=60)
        ids = [x.get("ext_id") for x in fused]
        self.assertIn("a", ids)
        self.assertIn("b", ids)
        self.assertIn("c", ids)
        self.assertEqual(3, len(ids))

    def test_inmemory_delete_by_doc_name(self):
        store = InMemoryVectorStore()
        texts = ["hello world", "foo bar"]
        embs = [[0.1, 0.2], [0.2, 0.3]]
        store.add_texts(texts=texts, embeddings=embs, namespace="ns1", doc_name="doc_a")
        deleted = store.delete_by_doc_name(doc_name="doc_a", namespace="ns1")
        self.assertEqual(2, deleted)
        remains = store.similarity_search_by_vector([0.1, 0.2], k=5, namespace="ns1")
        self.assertEqual([], remains)

    def test_inmemory_delete_by_doc_name_with_doc_version(self):
        store = InMemoryVectorStore()
        store.add_texts(
            texts=["v1 content", "v2 content"],
            embeddings=[[0.1, 0.2], [0.2, 0.3]],
            namespace="ns1",
            doc_name="doc_a",
            metadatas=[{"doc_version": "v1"}, {"doc_version": "v2"}],
        )
        deleted = store.delete_by_doc_name(doc_name="doc_a", namespace="ns1", doc_version="v1")
        self.assertEqual(1, deleted)
        remains = store.keyword_search("content", k=5, namespace="ns1")
        self.assertEqual(1, len(remains))
        self.assertEqual("v2", remains[0].get("metadata", {}).get("doc_version"))


if __name__ == "__main__":
    unittest.main()
