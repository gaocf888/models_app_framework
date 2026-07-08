import unittest
from unittest.mock import MagicMock

from app.rag.embedding_service import rag_cross_encoder_load_kwargs
from app.rag.rag_service import RAGService, _hit_namespace_allowed, _normalize_excluded_namespaces
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

    def test_normalize_excluded_namespaces(self):
        excluded = _normalize_excluded_namespaces(
            ["nl2sql_schema", "nl2sql_biz_knowledge", "nl2sql_qa_examples"]
        )
        self.assertEqual(
            excluded,
            {"nl2sql_schema", "nl2sql_biz_knowledge", "nl2sql_qa_examples"},
        )

    def test_hit_namespace_allowed_respects_exclude_set(self):
        excluded = _normalize_excluded_namespaces(["nl2sql_schema"]) or set()
        self.assertFalse(_hit_namespace_allowed({"namespace": "nl2sql_schema"}, excluded))
        self.assertTrue(_hit_namespace_allowed({"namespace": "global"}, excluded))

    def test_rag_cross_encoder_load_kwargs_qwen_uses_left_padding(self):
        kwargs = rag_cross_encoder_load_kwargs(
            trust_remote_code=False,
            model_id="/workspace/models/rerank/Qwen3-Reranker-0.6B",
        )
        self.assertTrue(kwargs["trust_remote_code"])
        self.assertEqual(kwargs["processor_kwargs"], {"padding_side": "left"})
        self.assertEqual(kwargs["tokenizer_kwargs"], {"padding_side": "left"})

    def test_rerank_skips_empty_doc_text(self):
        svc = RAGService.__new__(RAGService)
        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = [0.9]
        mock_reranker.device = "cpu"
        svc._get_reranker = lambda: mock_reranker  # type: ignore[method-assign]
        hits = [
            {"ext_id": "a", "text": "  "},
            {"ext_id": "b", "text": "valid chunk"},
        ]
        out = svc._rerank("query", hits)
        mock_reranker.predict.assert_called_once_with([["query", "valid chunk"]], batch_size=1)
        self.assertEqual(out[0]["ext_id"], "b")
        self.assertEqual(out[0]["_rerank_score"], 0.9)

    def test_rerank_predict_failure_keeps_rrf_order(self):
        svc = RAGService.__new__(RAGService)
        mock_reranker = MagicMock()
        mock_reranker.predict.side_effect = RuntimeError("cannot reshape tensor")
        mock_reranker.device = "cpu"
        svc._get_reranker = lambda: mock_reranker  # type: ignore[method-assign]
        hits = [{"ext_id": "a", "text": "alpha"}, {"ext_id": "b", "text": "beta"}]
        out = svc._rerank("query", hits)
        self.assertEqual([h["ext_id"] for h in out], ["a", "b"])
        self.assertNotIn("_rerank_score", out[0])


if __name__ == "__main__":
    unittest.main()
