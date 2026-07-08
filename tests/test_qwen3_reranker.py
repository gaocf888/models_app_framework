import unittest
from unittest.mock import MagicMock, patch

from app.rag.qwen3_reranker import DEFAULT_INSTRUCTION, Qwen3Reranker, format_qwen3_rerank_input


class TestQwen3Reranker(unittest.TestCase):
    def test_format_input(self) -> None:
        text = format_qwen3_rerank_input(
            "Given a web search query, retrieve relevant passages that answer the query",
            "地面沉降检测",
            "InSAR 可用于沉降监测",
        )
        self.assertIn("<Query>: 地面沉降检测", text)
        self.assertIn("<Document>: InSAR 可用于沉降监测", text)

    def test_predict_delegates_to_score_batch(self) -> None:
        reranker = Qwen3Reranker.__new__(Qwen3Reranker)
        reranker._score_batch = MagicMock(return_value=[0.8, 0.2])  # type: ignore[method-assign]
        scores = reranker.predict([["q1", "d1"], ["q2", "d2"]], batch_size=2)
        self.assertEqual([0.8, 0.2], scores)
        reranker._score_batch.assert_called_once()

    def test_encode_pair_returns_int_token_ids(self) -> None:
        reranker = Qwen3Reranker.__new__(Qwen3Reranker)
        reranker._instruction = DEFAULT_INSTRUCTION
        reranker._max_length = 8192
        reranker._prefix_tokens = [1, 2]
        reranker._suffix_tokens = [3, 4]
        reranker._max_body_tokens = 512
        mock_tok = MagicMock()
        mock_tok.encode.return_value = [10, 11, 12]
        reranker._tokenizer = mock_tok
        ids = reranker._encode_pair("查询", "文档")
        self.assertEqual([1, 2, 10, 11, 12, 3, 4], ids)
        mock_tok.encode.assert_called_once()

    @patch("app.rag.qwen3_reranker.Qwen3Reranker._score_batch", return_value=[0.95])
    def test_rag_service_uses_native_qwen_path(self, _mock_score: MagicMock) -> None:
        from app.rag.rag_service import RAGService

        svc = RAGService.__new__(RAGService)
        mock_reranker = MagicMock()
        mock_reranker.predict.return_value = [0.95]
        mock_reranker.device = "cuda:0"
        svc._get_reranker = lambda: mock_reranker  # type: ignore[method-assign]
        hits = [{"ext_id": "b", "text": "valid chunk"}]
        out = svc._rerank("query", hits)
        self.assertEqual("b", out[0]["ext_id"])
        self.assertEqual(0.95, out[0]["_rerank_score"])


if __name__ == "__main__":
    unittest.main()
