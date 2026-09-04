"""MIS-TEI 响应解析单测（无网络）。"""

from __future__ import annotations

import unittest

from app.rag.mis_tei_client import parse_embed_response, parse_rerank_scores


class TestMisTeiClientParse(unittest.TestCase):
    def test_parse_embed_single_vector(self) -> None:
        out = parse_embed_response([0.1, 0.2, 0.3])
        self.assertEqual(1, len(out))
        self.assertEqual([0.1, 0.2, 0.3], out[0])

    def test_parse_embed_batch(self) -> None:
        out = parse_embed_response([[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(2, len(out))
        self.assertEqual(2, len(out[0]))

    def test_parse_embed_openai_style(self) -> None:
        payload = {
            "data": [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
        }
        out = parse_embed_response(payload)
        self.assertEqual([[1.0, 0.0], [0.0, 1.0]], out)

    def test_parse_rerank_reorders_by_index(self) -> None:
        payload = [
            {"index": 1, "score": 0.9},
            {"index": 0, "score": 0.1},
        ]
        scores = parse_rerank_scores(payload, n_texts=2)
        self.assertEqual([0.1, 0.9], scores)


if __name__ == "__main__":
    unittest.main()
