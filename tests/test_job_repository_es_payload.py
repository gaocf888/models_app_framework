import json
import unittest

from app.rag.job_repository import JobRepository


class TestJobRepositoryEsPayload(unittest.TestCase):
    def test_prepare_and_parse_es_document(self) -> None:
        payload = {
            "job_id": "j1",
            "status": "SUCCESS",
            "metrics": {
                "step_durations_ms": [{"doc_name": "d1", "step": "parse", "ms": 12}],
                "doc_stats": [{"doc_name": "d1", "stats": {"chunk_count": 3}}],
            },
            "documents": [{"doc_name": "d1", "content": "hello", "metadata": {"mineru_job_id": "x"}}],
        }
        es_body = JobRepository._prepare_es_document(payload)
        self.assertNotIn("metrics", es_body)
        self.assertNotIn("documents", es_body)
        self.assertIn("metrics_json", es_body)
        self.assertIn("documents_json", es_body)

        roundtrip = JobRepository._parse_es_source(es_body)
        self.assertEqual(roundtrip["metrics"], payload["metrics"])
        self.assertEqual(roundtrip["documents"], payload["documents"])

    def test_parse_legacy_v1_source(self) -> None:
        legacy = {
            "job_id": "j2",
            "metrics": {"step_durations_ms": {"doc_a:parse": 99}},
            "documents": [],
        }
        parsed = JobRepository._parse_es_source(legacy)
        self.assertEqual(parsed["metrics"]["step_durations_ms"], {"doc_a:parse": 99})


if __name__ == "__main__":
    unittest.main()
