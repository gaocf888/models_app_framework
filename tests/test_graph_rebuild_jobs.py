import unittest
from unittest.mock import patch

from app.graph.rebuild_jobs import GraphRebuildJobRunner, GraphRebuildJobStatus


class TestGraphRebuildJobs(unittest.TestCase):
    @patch("app.graph.rebuild_jobs.GraphAdminService")
    def test_submit_and_complete_job(self, mock_admin_cls):
        mock_admin_cls.return_value.rebuild.return_value = {
            "rebuilt_docs": 1,
            "rebuilt_chunks": 3,
            "skipped_docs": 0,
        }
        runner = GraphRebuildJobRunner()
        job = runner.submit(mode="incremental", namespace="ns1", doc_names=["doc_a"])

        import time

        deadline = time.time() + 5
        while time.time() < deadline:
            current = runner.get_job(job.job_id)
            assert current is not None
            if current.status in {GraphRebuildJobStatus.SUCCESS, GraphRebuildJobStatus.FAILED}:
                break
            time.sleep(0.05)

        final = runner.get_job(job.job_id)
        assert final is not None
        self.assertEqual(GraphRebuildJobStatus.SUCCESS, final.status)
        self.assertEqual(1, final.metrics.get("rebuilt_docs"))


if __name__ == "__main__":
    unittest.main()
