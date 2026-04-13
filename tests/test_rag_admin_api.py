import asyncio
import unittest
from unittest.mock import MagicMock, patch

from fastapi import BackgroundTasks, HTTPException

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

    @patch("app.api.rag_admin.get_app_config")
    def test_move_document_namespace_success(self, mock_gc):
        mock_gc.return_value.rag.graph.enabled = False
        returned = {
            "doc_name": "doc_a",
            "doc_version": "v1",
            "tenant_id": None,
            "dataset_id": "ds_1",
            "namespace": "ns_b",
            "source_type": "text",
            "source_uri": None,
            "description": None,
            "chunk_count": 3,
            "pipeline_version": "p1",
            "status": "ready",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-04-13T00:00:00Z",
            "last_job_id": "j1",
            "last_job_type": "upsert",
            "last_job_status": "SUCCESS",
            "metadata": {"a": 1},
            "error": None,
        }

        class _DocRepo:
            def move_document_to_namespace(self, doc_name, **kwargs):
                self.kwargs = kwargs
                return returned

        class _Ingest:
            def reassign_namespace_for_doc(self, **kwargs):
                self.rkwargs = kwargs
                return 5

        ingest = _Ingest()
        repo = _DocRepo()
        with patch("app.api.rag_admin._get_doc_repo", return_value=repo):
            with patch("app.api.rag_admin._get_service", return_value=ingest):
                body = _to_dict(
                    asyncio.run(
                        rag_admin.move_document_namespace(
                            rag_admin.MoveDocumentNamespaceRequest(
                                doc_name="doc_a",
                                from_namespace="ns_a",
                                to_namespace="ns_b",
                            ),
                            BackgroundTasks(),
                        )
                    )
                )
        self.assertTrue(body.get("ok"))
        self.assertEqual(5, body.get("chunks_updated"))
        self.assertEqual("ns_b", body["document"]["namespace"])
        self.assertEqual("ns_a", ingest.rkwargs.get("from_namespace"))
        self.assertEqual("ns_b", ingest.rkwargs.get("to_namespace"))
        self.assertFalse(body.get("graph_repair_scheduled"))

    def test_move_document_namespace_not_found(self):
        class _DocRepo:
            def move_document_to_namespace(self, doc_name, **kwargs):
                raise LookupError("document not found for given filters")

        class _Ingest:
            def reassign_namespace_for_doc(self, **kwargs):
                return 0

        with patch("app.api.rag_admin._get_doc_repo", return_value=_DocRepo()):
            with patch("app.api.rag_admin._get_service", return_value=_Ingest()):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(
                        rag_admin.move_document_namespace(
                            rag_admin.MoveDocumentNamespaceRequest(
                                doc_name="missing",
                                from_namespace="x",
                                to_namespace="y",
                            ),
                            BackgroundTasks(),
                        )
                    )
        self.assertEqual(404, ctx.exception.status_code)

    def test_move_document_namespace_same_partition_400(self):
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(
                rag_admin.move_document_namespace(
                    rag_admin.MoveDocumentNamespaceRequest(
                        doc_name="a",
                        from_namespace="same",
                        to_namespace="same",
                    ),
                    BackgroundTasks(),
                )
            )
        self.assertEqual(400, ctx.exception.status_code)

    @patch("app.api.rag_admin.get_app_config")
    def test_move_document_namespace_schedules_graph_when_enabled(self, mock_gc):
        mock_gc.return_value.rag.graph.enabled = True
        returned = {
            "doc_name": "d1",
            "doc_version": "v1",
            "tenant_id": None,
            "dataset_id": "ds_x",
            "namespace": "to_ns",
            "source_type": "text",
            "source_uri": None,
            "description": None,
            "chunk_count": 1,
            "pipeline_version": "p1",
            "status": "ready",
            "created_at": "t",
            "updated_at": "t",
            "last_job_id": None,
            "last_job_type": None,
            "last_job_status": None,
            "metadata": {},
            "error": None,
        }

        class _DocRepo:
            def move_document_to_namespace(self, doc_name, **kwargs):
                return returned

        class _Ingest:
            def reassign_namespace_for_doc(self, **kwargs):
                return 1

        bt = MagicMock()
        with patch("app.api.rag_admin._get_doc_repo", return_value=_DocRepo()):
            with patch("app.api.rag_admin._get_service", return_value=_Ingest()):
                body = _to_dict(
                    asyncio.run(
                        rag_admin.move_document_namespace(
                            rag_admin.MoveDocumentNamespaceRequest(
                                doc_name="d1",
                                from_namespace="a",
                                to_namespace="b",
                            ),
                            bt,
                        )
                    )
                )
        bt.add_task.assert_called_once()
        self.assertEqual(bt.add_task.call_args[0][0].__name__, "run_graph_resync_after_namespace_move")
        self.assertTrue(body.get("graph_repair_scheduled"))


if __name__ == "__main__":
    unittest.main()
