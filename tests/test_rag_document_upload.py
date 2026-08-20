from __future__ import annotations

import asyncio
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.rag.asset_storage import RagAssetStorage
from app.rag.content_url_fetch import materialize_document_content_from_url
from app.rag.document_repository import DocumentRepository
from app.rag.models import DocumentSource
from app.rag.original_docs import (
    guess_source_type,
    looks_like_object_ref,
    parse_object_ref,
    resolve_namespace_kb_for_ingest,
)
from app.api import rag_admin


class TestOriginalObjectRef(unittest.TestCase):
    def test_parse_minio_uri(self):
        kind, bucket, key = parse_object_ref("minio://rag-assets/rag-docs/法规/doc/v1/original.pdf")
        self.assertEqual("minio", kind)
        self.assertEqual("rag-assets", bucket)
        self.assertEqual("rag-docs/法规/doc/v1/original.pdf", key)

    def test_parse_local_uri(self):
        kind, bucket, path = parse_object_ref("local:C:/tmp/a.pdf")
        self.assertEqual("local", kind)
        self.assertIsNone(bucket)
        self.assertEqual("C:/tmp/a.pdf", path)

    def test_looks_like_object_ref(self):
        self.assertTrue(looks_like_object_ref("minio://b/k"))
        self.assertTrue(looks_like_object_ref("local:/tmp/a.pdf"))
        self.assertFalse(looks_like_object_ref("https://example.com/a.pdf"))
        self.assertFalse(looks_like_object_ref("hello text"))

    def test_guess_source_type(self):
        self.assertEqual("pdf", guess_source_type("a.PDF"))
        self.assertEqual("docx", guess_source_type("手册.docx"))
        self.assertEqual("markdown", guess_source_type("readme.md"))


class TestOriginalStorageLocal(unittest.TestCase):
    def test_upload_get_delete_and_materialize_text(self):
        storage = RagAssetStorage()
        storage._minio = None
        storage._backend = "local"
        stored = storage.upload_original(
            data=b"hello-original",
            namespace="法规",
            doc_name="sample",
            filename="sample.txt",
            content_type="text/plain",
        )
        uri = stored["source_uri"]
        self.assertTrue(looks_like_object_ref(uri))
        data, _ = storage.get_original_bytes(uri)
        self.assertEqual(b"hello-original", data)

        doc = DocumentSource(
            dataset_id="default",
            doc_name="sample",
            namespace="法规",
            content=uri,
            source_type="text",
            source_uri=uri,
        )
        new_doc, tmp = materialize_document_content_from_url(doc)
        self.assertIsNone(tmp)
        self.assertEqual("hello-original", new_doc.content)
        self.assertTrue(storage.delete_original(uri))


class TestDocNameContains(unittest.TestCase):
    def test_file_repo_contains_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = DocumentRepository(state_dir=tmp)
            repo._use_es = False
            repo._client = None
            repo.upsert(
                "k1",
                {"doc_name": "北京市地下水超采", "namespace": "法规", "updated_at": "2026-01-02"},
            )
            repo.upsert(
                "k2",
                {"doc_name": "历史灾险案例", "namespace": "法规", "updated_at": "2026-01-01"},
            )
            rows = repo.list(limit=20, offset=0, namespace="法规", doc_name_contains="地下水")
            self.assertEqual(1, len(rows))
            self.assertEqual("北京市地下水超采", rows[0]["doc_name"])
            exact = repo.list(limit=20, offset=0, namespace="法规", doc_name="历史灾险案例")
            self.assertEqual(1, len(exact))


class TestRequireNamespace(unittest.TestCase):
    def test_upload_blank_namespace_400(self):
        with self.assertRaises(ValueError) as ctx:
            rag_admin._ensure_ingest_namespace("  ", always=True)
        self.assertIn("namespace is required", str(ctx.exception))

    def test_ingest_requires_namespace_when_flag_on(self):
        cfg = MagicMock()
        cfg.rag.require_namespace = True
        with patch("app.rag.original_docs.get_app_config", return_value=cfg):
            with self.assertRaises(ValueError):
                rag_admin._ensure_ingest_namespace(None)

    def test_ingest_allows_blank_when_flag_off(self):
        cfg = MagicMock()
        cfg.rag.require_namespace = False
        with patch("app.rag.original_docs.get_app_config", return_value=cfg):
            self.assertIsNone(rag_admin._ensure_ingest_namespace(None))
            self.assertEqual("法规", rag_admin._ensure_ingest_namespace("法规"))


class TestInheritNamespaceKb(unittest.TestCase):
    def test_inherit_from_existing_row(self):
        class _Repo:
            def list_namespace_kb_configs(self):
                return [{"namespace": "法规", "namespace_kb_enabled": False, "namespace_kb_priority": 3}]

        with patch("app.rag.document_repository.DocumentRepository", return_value=_Repo()):
            enabled, priority = resolve_namespace_kb_for_ingest("法规", None, None)
        self.assertFalse(enabled)
        self.assertEqual(3, priority)

    def test_explicit_fields_win(self):
        enabled, priority = resolve_namespace_kb_for_ingest("法规", True, 2)
        self.assertTrue(enabled)
        self.assertEqual(2, priority)


class TestJobsIngestNamespaceFlag(unittest.TestCase):
    def test_submit_job_400_when_namespace_required(self):
        cfg = MagicMock()
        cfg.rag.require_namespace = True
        req = rag_admin.IngestionJobRequest(
            documents=[
                rag_admin.IngestionJobDocumentRequest(
                    dataset_id="ds",
                    doc_name="a",
                    content="hello",
                    namespace=None,
                )
            ]
        )
        with patch("app.api.rag_admin.get_app_config", return_value=cfg):
            with patch("app.rag.original_docs.get_app_config", return_value=cfg):
                with self.assertRaises(HTTPException) as ctx:
                    asyncio.run(rag_admin.submit_ingestion_job(req))
        self.assertEqual(400, ctx.exception.status_code)


if __name__ == "__main__":
    unittest.main()
