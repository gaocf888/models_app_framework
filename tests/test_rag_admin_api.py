import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api import rag_admin


def _to_dict(model_obj):
    if hasattr(model_obj, "model_dump"):
        return model_obj.model_dump()
    return model_obj.dict()


class TestRagAdminMetaApi(unittest.TestCase):
    def test_get_job_documents_success(self):
        mock_payload = {
            "job_id": "job_1",
            "documents": [
                {
                    "dataset_id": "ds_1",
                    "doc_name": "doc_a",
                    "namespace": "ns_a",
                    "source_type": "markdown",
                    "source_uri": "s3://bucket/a.md",
                    "description": "demo",
                    "replace_if_exists": True,
                    "metadata": {"k": "v"},
                }
            ],
        }
        class _JobRepo:
            @staticmethod
            def get(job_id):
                return mock_payload

        with patch("app.api.rag_admin._get_job_repo", return_value=_JobRepo()):
            body = _to_dict(asyncio.run(rag_admin.get_job_documents("job_1")))
        self.assertTrue(body.get("ok"))
        self.assertEqual("job_1", body.get("job_id"))
        self.assertEqual(1, len(body.get("documents", [])))
        self.assertEqual("doc_a", body["documents"][0]["doc_name"])

    def test_get_job_documents_not_found(self):
        class _JobRepo:
            @staticmethod
            def get(job_id):
                return None

        with patch("app.api.rag_admin._get_job_repo", return_value=_JobRepo()):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(rag_admin.get_job_documents("job_not_exists"))
        self.assertEqual(404, ctx.exception.status_code)
        self.assertIn("job not found", str(ctx.exception.detail))

    def test_list_documents_meta_with_namespace(self):
        mock_docs = [
            {
                "doc_name": "doc_a",
                "dataset_id": "ds_1",
                "namespace": "ns_a",
                "source_type": "text",
                "source_uri": None,
                "chunk_count": 7,
                "pipeline_version": "v1",
                "status": "SUCCESS",
                "updated_at": "2026-03-27T12:00:00Z",
                "metadata": {"lang": "zh"},
                "error": None,
            }
        ]
        class _DocRepo:
            def __init__(self):
                self.called = False
                self.last_args = None

            def list(self, limit, offset, namespace):
                self.called = True
                self.last_args = (limit, offset, namespace)
                return mock_docs

        repo = _DocRepo()
        with patch("app.api.rag_admin._get_doc_repo", return_value=repo):
            body = _to_dict(asyncio.run(rag_admin.list_document_meta(limit=10, offset=0, namespace="ns_a")))
        self.assertTrue(body.get("ok"))
        self.assertEqual(10, body.get("limit"))
        self.assertEqual(0, body.get("offset"))
        self.assertEqual("ns_a", body.get("namespace"))
        self.assertEqual(1, len(body.get("documents", [])))
        self.assertEqual("doc_a", body["documents"][0]["doc_name"])
        self.assertTrue(repo.called)
        self.assertEqual((10, 0, "ns_a"), repo.last_args)


if __name__ == "__main__":
    unittest.main()
