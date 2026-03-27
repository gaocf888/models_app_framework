import tempfile
import time
import unittest

from app.rag.ingestion_orchestrator import IngestionOrchestrator
from app.rag.models import DocumentSource


class _FakeIngestionService:
    def ingest_texts(
        self,
        dataset_id,
        texts,
        description=None,
        namespace=None,
        doc_name=None,
        replace_if_exists=True,
    ):
        if doc_name and "fail" in doc_name:
            raise RuntimeError("forced failure")
        return None

    def post_index_hook(
        self,
        dataset_id,
        texts,
        namespace=None,
        doc_name=None,
        doc_version="v1",
        replace_if_exists=True,
    ):
        return None

    def finalize_alias_version(self, namespace=None, doc_version=None):
        return None


class TestIngestionOrchestrator(unittest.TestCase):
    def test_job_success(self):
        with tempfile.TemporaryDirectory() as d:
            orch = IngestionOrchestrator(ingestion_service=_FakeIngestionService(), state_dir=d)
            try:
                docs = [
                    DocumentSource(
                        dataset_id="ds1",
                        doc_name="doc_ok",
                        namespace="ns1",
                        content="这是一个用于测试的文档内容。",
                        source_type="text",
                    )
                ]
                job_id = orch.submit_job(docs, operator="tester")
                final = None
                for _ in range(30):
                    final = orch.get_job(job_id)
                    if final and final.status.value in {"SUCCESS", "FAILED", "PARTIAL"}:
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(final)
                self.assertEqual("SUCCESS", final.status.value)
                self.assertEqual(1, final.metrics.get("documents_success"))
                steps = final.metrics.get("step_durations_ms") or {}
                self.assertTrue(any(k.endswith(":parse") for k in steps.keys()))
                self.assertTrue(any(k.endswith(":quality_check") for k in steps.keys()))
            finally:
                orch.close()

    def test_job_partial(self):
        with tempfile.TemporaryDirectory() as d:
            orch = IngestionOrchestrator(ingestion_service=_FakeIngestionService(), state_dir=d)
            try:
                docs = [
                    DocumentSource(
                        dataset_id="ds1",
                        doc_name="doc_ok",
                        namespace="ns1",
                        content="正常文档。",
                        source_type="text",
                    ),
                    DocumentSource(
                        dataset_id="ds1",
                        doc_name="doc_fail",
                        namespace="ns1",
                        content="会触发失败。",
                        source_type="text",
                    ),
                ]
                job_id = orch.submit_job(docs, operator="tester")
                final = None
                for _ in range(40):
                    final = orch.get_job(job_id)
                    if final and final.status.value in {"SUCCESS", "FAILED", "PARTIAL"}:
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(final)
                self.assertEqual("PARTIAL", final.status.value)
                self.assertEqual(1, final.metrics.get("documents_success"))
                self.assertEqual(1, final.metrics.get("documents_failed"))
            finally:
                orch.close()

    def test_list_and_count(self):
        with tempfile.TemporaryDirectory() as d:
            orch = IngestionOrchestrator(ingestion_service=_FakeIngestionService(), state_dir=d)
            try:
                docs = [
                    DocumentSource(
                        dataset_id="ds1",
                        doc_name="doc_1",
                        namespace="ns1",
                        content="内容1",
                        source_type="text",
                    )
                ]
                orch.submit_job(docs, operator="tester")
                orch.submit_job(docs, operator="tester")
                # 任务异步执行，不依赖完成状态，校验列表与计数接口即可
                self.assertGreaterEqual(orch.count_jobs(), 2)
                page = orch.list_jobs(limit=1, offset=0)
                self.assertEqual(1, len(page))
            finally:
                orch.close()


if __name__ == "__main__":
    unittest.main()

