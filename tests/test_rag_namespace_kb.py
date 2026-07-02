import unittest

from app.rag.models import ChunkRecord, DocumentSource
from app.rag.namespace_kb import (
    apply_priority_score_adjustment,
    apply_tiered_priority_order,
    build_chunk_metadatas,
    chunk_passes_kb_enabled_filter,
    finalize_retrieval_hits,
    normalize_namespace_kb_priority,
    resolve_namespace_kb_fields,
)
from app.rag.rag_service import RAGService, _hit_base_score
from app.rag.vector_store import InMemoryVectorStore


class TestNamespaceKbHelpers(unittest.TestCase):
    def test_resolve_defaults(self):
        self.assertEqual((True, 1), resolve_namespace_kb_fields(None, None))

    def test_priority_must_be_at_least_one(self):
        with self.assertRaises(ValueError):
            normalize_namespace_kb_priority(0)

    def test_api_fields_override_metadata(self):
        doc = DocumentSource(
            dataset_id="ds",
            doc_name="d1",
            namespace="ns_a",
            content="x",
            namespace_kb_enabled=True,
            namespace_kb_priority=2,
            metadata={"namespace_kb_enabled": False, "namespace_kb_priority": 99},
        )
        chunks = [ChunkRecord(chunk_id="c1", chunk_index=0, text="hello")]
        metas = build_chunk_metadatas(doc, chunks)
        self.assertTrue(metas[0]["namespace_kb_enabled"])
        self.assertEqual(2, metas[0]["namespace_kb_priority"])

    def test_build_chunk_metadatas(self):
        doc = DocumentSource(
            dataset_id="ds",
            doc_name="d1",
            namespace="ns_a",
            content="x",
            namespace_kb_enabled=False,
            namespace_kb_priority=3,
            metadata={"tag": "t"},
        )
        chunks = [ChunkRecord(chunk_id="c1", chunk_index=0, text="hello", metadata={"extra": 1})]
        metas = build_chunk_metadatas(doc, chunks)
        self.assertEqual(1, len(metas))
        self.assertFalse(metas[0]["namespace_kb_enabled"])
        self.assertEqual(3, metas[0]["namespace_kb_priority"])
        self.assertEqual("t", metas[0]["tag"])
        self.assertEqual(1, metas[0]["extra"])

    def test_chunk_passes_kb_enabled_filter_string_false(self):
        self.assertFalse(chunk_passes_kb_enabled_filter({"namespace_kb_enabled": "false"}))
        self.assertTrue(chunk_passes_kb_enabled_filter({"namespace_kb_enabled": "true"}))

    def test_priority_adjustment_smaller_is_better(self):
        high = apply_priority_score_adjustment(1.0, {"namespace_kb_priority": 1}, 0.1)
        low = apply_priority_score_adjustment(1.0, {"namespace_kb_priority": 5}, 0.1)
        self.assertGreater(high, low)

    def test_tiered_priority_prefers_lower_priority_tier(self):
        hits = [
            {"text": "low semantic high pri", "score": 0.2, "metadata": {"namespace_kb_priority": 1}},
            {"text": "high semantic low pri", "score": 0.9, "metadata": {"namespace_kb_priority": 5}},
        ]
        ordered = apply_tiered_priority_order(hits, 2, score_getter=_hit_base_score)
        self.assertEqual(1, ordered[0]["metadata"]["namespace_kb_priority"])


class _FakeEmbeddingService:
    def embed_text(self, query):
        return [1.0, 0.0]


class TestNamespaceKbRecall(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryVectorStore()
        self.store.add_texts(
            texts=["enabled high priority", "disabled chunk"],
            embeddings=[[1.0, 0.0], [0.9, 0.1]],
            namespace="ns1",
            doc_name="doc1",
            metadatas=[
                {"namespace_kb_enabled": True, "namespace_kb_priority": 1},
                {"namespace_kb_enabled": False, "namespace_kb_priority": 1},
            ],
        )
        self.store.add_texts(
            texts=["enabled low priority"],
            embeddings=[[0.95, 0.05]],
            namespace="ns2",
            doc_name="doc2",
            metadatas=[{"namespace_kb_enabled": True, "namespace_kb_priority": 5}],
        )
        self.svc = RAGService(embedding_service=_FakeEmbeddingService(), store_provider=_FakeStoreProvider(self.store))
        self.svc._cfg.hybrid.enabled = False
        self.svc._cfg.namespace_kb_priority_boost = 0.2

    def test_disabled_chunks_not_retrieved(self):
        chunks = self.svc.retrieve_chunks("enabled", top_k=5, namespace="ns1", use_hybrid=False)
        self.assertEqual(1, len(chunks))
        self.assertTrue(chunk_passes_kb_enabled_filter(chunks[0].metadata))

    def test_cross_namespace_priority_order(self):
        chunks = self.svc.retrieve_chunks("enabled", top_k=2, namespace=None, use_hybrid=False)
        self.assertGreaterEqual(len(chunks), 1)
        if len(chunks) >= 2:
            p0 = chunks[0].metadata.get("namespace_kb_priority", 1)
            p1 = chunks[1].metadata.get("namespace_kb_priority", 1)
            self.assertLessEqual(p0, p1)

    def test_retrieved_chunk_score_reflects_priority_adjustment(self):
        chunks = self.svc.retrieve_chunks("enabled", top_k=2, namespace=None, use_hybrid=False)
        self.assertGreaterEqual(len(chunks), 1)
        if chunks[0].score is not None and chunks[0].metadata.get("namespace_kb_priority") == 1:
            self.assertGreater(chunks[0].score, 0.0)

    def test_tiered_mode_orders_by_priority_before_top_k(self):
        self.svc._cfg.namespace_kb_priority_tiered = True
        self.svc._cfg.namespace_kb_priority_boost = 0.0
        chunks = self.svc.retrieve_chunks("enabled", top_k=1, namespace=None, use_hybrid=False)
        self.assertEqual(1, len(chunks))
        self.assertEqual(1, chunks[0].metadata.get("namespace_kb_priority"))

    def test_update_namespace_kb_config(self):
        updated = self.store.update_namespace_kb_config("ns1", enabled=False, priority=9)
        self.assertEqual(2, updated)
        chunks = self.svc.retrieve_chunks("enabled", top_k=5, namespace="ns1", use_hybrid=False)
        self.assertEqual(0, len(chunks))

    def test_delete_by_namespace(self):
        deleted = self.store.delete_by_namespace("ns1")
        self.assertEqual(2, deleted)
        chunks = self.svc.retrieve_chunks("enabled", top_k=5, namespace="ns1", use_hybrid=False)
        self.assertEqual(0, len(chunks))
        remaining = self.svc.retrieve_chunks("enabled", top_k=5, namespace="ns2", use_hybrid=False)
        self.assertEqual(1, len(remaining))

    def test_finalize_retrieval_hits_truncates_after_ranking(self):
        hits = [
            {"text": "a", "score": 0.1, "metadata": {"namespace_kb_priority": 5}},
            {"text": "b", "score": 0.9, "metadata": {"namespace_kb_priority": 1}},
            {"text": "c", "score": 0.8, "metadata": {"namespace_kb_priority": 1}},
        ]
        out = finalize_retrieval_hits(
            hits,
            namespace=None,
            priority_boost=0.2,
            priority_tiered=False,
            k_out=2,
            score_getter=_hit_base_score,
        )
        self.assertEqual(2, len(out))
        self.assertEqual(1, out[0]["metadata"]["namespace_kb_priority"])


class _FakeStoreProvider:
    def __init__(self, store):
        self._store = store

    def get_default_store(self):
        return self._store


if __name__ == "__main__":
    unittest.main()
