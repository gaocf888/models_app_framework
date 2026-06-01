import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from app.api import graph_admin


class TestGraphAdminAPI(unittest.IsolatedAsyncioTestCase):
    @patch("app.graph.admin_service.get_app_config")
    async def test_health_when_disabled(self, mock_cfg):
        mock_cfg.return_value.rag.graph.enabled = False
        resp = await graph_admin.graph_health()
        self.assertFalse(resp.enabled)
        self.assertFalse(resp.ok)

    @patch("app.api.graph_admin.get_app_config")
    async def test_rebuild_async_returns_job_id(self, mock_cfg):
        from app.core.config import GraphRAGConfig, RAGConfig

        rag_cfg = RAGConfig()
        rag_cfg.graph = GraphRAGConfig(enabled=True, uri="bolt://x", username="u", password="p")
        mock_cfg.return_value.rag = rag_cfg

        with patch("app.api.graph_admin.GraphRebuildJobRunner") as mock_runner_cls:
            mock_job = MagicMock()
            mock_job.job_id = "job_test_1"
            mock_job.status.value = "PENDING"
            mock_runner_cls.get_default.return_value.submit.return_value = mock_job

            from app.api.graph_admin import GraphRebuildRequest, graph_rebuild

            resp = await graph_rebuild(
                GraphRebuildRequest(
                    mode="incremental",
                    namespace="ns",
                    doc_names=["doc_a"],
                    async_mode=True,
                )
            )
            self.assertTrue(resp.async_mode)
            self.assertEqual("job_test_1", resp.job_id)

    @patch("app.api.graph_admin.get_app_config")
    async def test_stats_returns_503_when_disabled(self, mock_cfg):
        mock_cfg.return_value.rag.graph.enabled = False
        with self.assertRaises(HTTPException) as ctx:
            await graph_admin.graph_stats()
        self.assertEqual(503, ctx.exception.status_code)


if __name__ == "__main__":
    unittest.main()
