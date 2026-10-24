import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.rag.document_repository import (
    DocumentRepository,
    classify_pipeline_status,
    make_document_storage_key,
)
from app.rag.namespace_kb import NL2SQL_KB_ADMIN_SIDEBAR_EXCLUDED_NAMESPACES
from app.rag.vector_store import InMemoryVectorStore


def _mock_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.rag.vector_store_type = "inmemory"
    cfg.rag.es.docs_index_name = "docs"
    cfg.rag.es.docs_index_version = "1"
    cfg.rag.es.docs_index_alias = "docs_alias"
    cfg.rag.ingestion.tenant_id_default = "__tenant__"
    return cfg


class TestVectorStoreListChunks(unittest.TestCase):
    def test_list_chunk_texts_sorts_by_chunk_index(self) -> None:
        store = InMemoryVectorStore()
        store.add_texts(
            ["third", "first", "second"],
            [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
            namespace="ns",
            doc_name="doc",
            metadatas=[
                {"doc_version": "v1", "chunk_index": 2},
                {"doc_version": "v1", "chunk_index": 0},
                {"doc_version": "v1", "chunk_index": 1},
            ],
        )
        texts = store.list_chunk_texts_for_document("doc", "ns", "v1")
        self.assertEqual(texts, ["first", "second", "third"])


class TestDocumentRepositoryNamespaceMove(unittest.TestCase):
    def test_make_storage_key(self) -> None:
        k = make_document_storage_key(
            "n1",
            namespace=None,
            tenant_id=None,
            doc_version=None,
            tenant_id_fallback="__tenant__",
        )
        self.assertEqual(k, "__tenant__::__default__::n1::v1")

    @patch("app.rag.document_repository.get_app_config")
    def test_move_document_to_namespace_changes_key(self, gc: MagicMock) -> None:
        gc.return_value = _mock_cfg()
        with tempfile.TemporaryDirectory() as d:
            repo = DocumentRepository(state_dir=d)
            old_key = make_document_storage_key(
                "doc1",
                namespace="ns_a",
                tenant_id=None,
                doc_version="v1",
                tenant_id_fallback="__tenant__",
            )
            repo.upsert(
                old_key,
                {
                    "doc_name": "doc1",
                    "doc_version": "v1",
                    "tenant_id": None,
                    "dataset_id": "ds",
                    "namespace": "ns_a",
                    "source_type": "text",
                    "chunk_count": 2,
                    "metadata": {},
                    "status": "ready",
                    "created_at": "t0",
                    "updated_at": "t0",
                },
            )
            out = repo.move_document_to_namespace(
                "doc1",
                from_namespace="ns_a",
                to_namespace="ns_b",
            )
            self.assertEqual(out["namespace"], "ns_b")
            new_key = make_document_storage_key(
                "doc1",
                namespace="ns_b",
                tenant_id=None,
                doc_version="v1",
                tenant_id_fallback="__tenant__",
            )
            self.assertIsNotNone(repo.get(new_key))
            self.assertIsNone(repo.get(old_key))

    @patch("app.rag.document_repository.get_app_config")
    def test_move_document_to_namespace_ambiguous(self, gc: MagicMock) -> None:
        gc.return_value = _mock_cfg()
        with tempfile.TemporaryDirectory() as d:
            repo = DocumentRepository(state_dir=d)
            for ds in ("ds1", "ds2"):
                # 存储主键任意唯一即可；匹配逻辑看 payload
                key = f"row_{ds}"
                # 同一默认 namespace、同名同版本，但不同 dataset —— 不传 dataset_id 过滤时会命中 2 条
                repo.upsert(
                    key,
                    {
                        "doc_name": "same",
                        "doc_version": "v1",
                        "dataset_id": ds,
                        "namespace": None,
                        "source_type": "text",
                        "chunk_count": 0,
                        "metadata": {},
                        "status": "ready",
                        "created_at": "t",
                        "updated_at": "t",
                    },
                )
            with self.assertRaises(ValueError):
                repo.move_document_to_namespace(
                    "same",
                    from_namespace=None,
                    to_namespace="z",
                )

    @patch("app.rag.document_repository.get_app_config")
    def test_list_namespace_kb_configs_uses_latest_updated_doc(self, gc: MagicMock) -> None:
        gc.return_value = _mock_cfg()
        with tempfile.TemporaryDirectory() as d:
            repo = DocumentRepository(state_dir=d)
            td = "__tenant__"
            for name, pri, ts in (
                ("old_doc", 1, "2026-01-01T00:00:00+00:00"),
                ("new_doc", 2, "2026-07-02T00:00:00+00:00"),
            ):
                key = make_document_storage_key(
                    name,
                    namespace="n1",
                    tenant_id=None,
                    doc_version="v1",
                    tenant_id_fallback=td,
                )
                repo.upsert(
                    key,
                    {
                        "namespace": "n1",
                        "metadata": {
                            "namespace_kb_enabled": True,
                            "namespace_kb_priority": pri,
                        },
                        "updated_at": ts,
                    },
                )
            rows = repo.list_namespace_kb_configs()
            self.assertEqual(1, len(rows))
            self.assertEqual("n1", rows[0]["namespace"])
            self.assertEqual(2, rows[0]["namespace_kb_priority"])
            self.assertEqual(2, rows[0]["document_count"])


def _pipeline_row(
    name: str,
    *,
    status: str,
    last_job_status: str | None,
    namespace: str = "法规",
    tenant_id: str = "__tenant__",
) -> tuple[str, dict]:
    key = make_document_storage_key(
        name,
        namespace=namespace,
        tenant_id=tenant_id,
        doc_version="v1",
        tenant_id_fallback=tenant_id,
    )
    payload = {
        "doc_name": name,
        "doc_version": "v1",
        "tenant_id": tenant_id,
        "dataset_id": "default",
        "namespace": namespace,
        "source_type": "pdf",
        "chunk_count": 0,
        "status": status,
        "last_job_status": last_job_status,
        "created_at": "2026-10-24T00:00:00Z",
        "updated_at": "2026-10-24T00:00:00Z",
        "metadata": {},
    }
    return key, payload


class TestDocumentRepositoryPipelineStatus(unittest.TestCase):
    def test_classify_ingesting_wins_over_status(self) -> None:
        self.assertEqual(
            "ingesting",
            classify_pipeline_status({"status": "SUCCESS", "last_job_status": "PENDING"}),
        )
        self.assertEqual(
            "ingesting",
            classify_pipeline_status({"status": "UPLOADED", "last_job_status": "RUNNING"}),
        )
        self.assertEqual(
            "uploaded",
            classify_pipeline_status({"status": "UPLOADED", "last_job_status": "UPLOADED"}),
        )
        self.assertEqual(
            "ingested",
            classify_pipeline_status({"status": "SUCCESS", "last_job_status": "SUCCESS"}),
        )
        self.assertEqual(
            "ingested",
            classify_pipeline_status({"status": "SUCCESS", "last_job_status": "RUNNING"}),
        )
        self.assertEqual(
            "failed",
            classify_pipeline_status({"status": "FAILED", "last_job_status": "FAILED"}),
        )
        self.assertEqual(
            "failed",
            classify_pipeline_status({"status": "FAILED", "last_job_status": "RUNNING"}),
        )

    @patch("app.rag.document_repository.get_app_config")
    def test_overview_pipeline_counts_and_list_filter(self, gc: MagicMock) -> None:
        gc.return_value = _mock_cfg()
        with tempfile.TemporaryDirectory() as d:
            repo = DocumentRepository(state_dir=d)
            rows = [
                _pipeline_row("only_uploaded", status="UPLOADED", last_job_status="UPLOADED"),
                _pipeline_row("ingesting_new", status="UPLOADED", last_job_status="PENDING"),
                _pipeline_row("reingest", status="SUCCESS", last_job_status="PENDING"),
                _pipeline_row("done", status="SUCCESS", last_job_status="SUCCESS"),
                _pipeline_row("legacy_running", status="SUCCESS", last_job_status="RUNNING"),
                _pipeline_row("broken", status="FAILED", last_job_status="FAILED"),
                _pipeline_row("other_ns", status="SUCCESS", last_job_status="SUCCESS", namespace="规程"),
            ]
            for key, payload in rows:
                repo.upsert(key, payload)

            stats = repo.overview(namespace="法规")
            self.assertEqual(6, stats["total_documents"])
            self.assertEqual(
                {"all": 6, "uploaded": 1, "ingesting": 2, "ingested": 2, "failed": 1},
                stats["pipeline_counts"],
            )

            uploaded = repo.list(limit=20, offset=0, namespace="法规", pipeline_status="uploaded")
            self.assertEqual(["only_uploaded"], [r["doc_name"] for r in uploaded])

            ingesting = repo.list(limit=20, offset=0, namespace="法规", pipeline_status="ingesting")
            self.assertEqual({"ingesting_new", "reingest"}, {r["doc_name"] for r in ingesting})

            ingested = repo.list(limit=20, offset=0, namespace="法规", pipeline_status="ingested")
            self.assertEqual({"done", "legacy_running"}, {r["doc_name"] for r in ingested})

            failed = repo.list(limit=20, offset=0, namespace="法规", pipeline_status="failed")
            self.assertEqual(["broken"], [r["doc_name"] for r in failed])

            all_rows = repo.list(limit=20, offset=0, namespace="法规", pipeline_status="all")
            self.assertEqual(6, len(all_rows))

    @patch("app.rag.document_repository.get_app_config")
    def test_pipeline_counts_ignore_list_status_filter(self, gc: MagicMock) -> None:
        gc.return_value = _mock_cfg()
        with tempfile.TemporaryDirectory() as d:
            repo = DocumentRepository(state_dir=d)
            for key, payload in [
                _pipeline_row("a", status="UPLOADED", last_job_status="UPLOADED"),
                _pipeline_row("b", status="SUCCESS", last_job_status="SUCCESS"),
            ]:
                repo.upsert(key, payload)
            # overview 不接收 pipeline_status：芯片数字始终是当前分类下的全量分桶
            stats = repo.overview(namespace="法规")
            listed = repo.list(limit=20, offset=0, namespace="法规", pipeline_status="uploaded")
            self.assertEqual(1, len(listed))
            self.assertEqual(2, stats["pipeline_counts"]["all"])
            self.assertEqual(1, stats["pipeline_counts"]["uploaded"])
            self.assertEqual(1, stats["pipeline_counts"]["ingested"])

    @patch("app.rag.document_repository.get_app_config")
    def test_overview_excludes_nl2sql_builtin_namespaces(self, gc: MagicMock) -> None:
        gc.return_value = _mock_cfg()
        excluded = list(NL2SQL_KB_ADMIN_SIDEBAR_EXCLUDED_NAMESPACES)
        with tempfile.TemporaryDirectory() as d:
            repo = DocumentRepository(state_dir=d)
            for key, payload in [
                _pipeline_row("biz", status="SUCCESS", last_job_status="SUCCESS", namespace="法规"),
                _pipeline_row(
                    "schema",
                    status="SUCCESS",
                    last_job_status="SUCCESS",
                    namespace="nl2sql_schema",
                ),
                _pipeline_row(
                    "biz_kb",
                    status="SUCCESS",
                    last_job_status="SUCCESS",
                    namespace="nl2sql_biz_knowledge",
                ),
                _pipeline_row(
                    "qa",
                    status="SUCCESS",
                    last_job_status="SUCCESS",
                    namespace="nl2sql_qa_examples",
                ),
            ]:
                repo.upsert(key, payload)

            full = repo.overview()
            self.assertEqual(4, full["total_documents"])
            self.assertEqual(4, full["pipeline_counts"]["all"])

            filtered = repo.overview(exclude_namespaces=excluded)
            self.assertEqual(1, filtered["total_documents"])
            self.assertEqual(1, filtered["pipeline_counts"]["all"])
            self.assertEqual(1, filtered["pipeline_counts"]["ingested"])
            self.assertEqual(["法规"], [b["key"] for b in filtered["by_namespace"]])

            listed = repo.list(limit=20, offset=0, exclude_namespaces=excluded)
            self.assertEqual(["biz"], [r["doc_name"] for r in listed])

            empty = repo.list(
                limit=20,
                offset=0,
                namespace="nl2sql_schema",
                exclude_namespaces=excluded,
            )
            self.assertEqual([], empty)


if __name__ == "__main__":
    unittest.main()
