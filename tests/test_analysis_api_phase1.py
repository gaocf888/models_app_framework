import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.api import analysis as analysis_api
from app.models.analysis import AnalysisNL2SQLRequest, AnalysisPayloadRequest


class TestAnalysisApiPhase1(unittest.TestCase):
    def test_run_with_payload_route_calls_service(self):
        fake_result = AsyncMock()
        fake_result = {
            "request_id": "anl_x",
            "analysis_type": "overheat_guidance",
            "summary": "ok",
            "structured_report": {},
            "evidence": {"used_rag": False, "rag_sources": [], "nl2sql_calls": [], "data_coverage": {}},
            "trace": {"plan_id": "p1", "node_latency_ms": {}, "template_versions": {}},
        }
        req = AnalysisPayloadRequest(
            user_id="user_api_payload",
            session_id="sess_api_payload",
            analysis_type="overheat_guidance",
            query="测试 payload 路由",
            payload={"x": 1},
        )
        with patch.object(
            analysis_api.service,
            "run_analysis_payload",
            new=AsyncMock(return_value=fake_result),
        ) as mock_call:
            body = asyncio.run(analysis_api.run_analysis_with_payload(req))
        self.assertEqual("overheat_guidance", body["analysis_type"])
        mock_call.assert_awaited_once()

    def test_run_with_nl2sql_route_calls_service(self):
        fake_result = {
            "request_id": "anl_y",
            "analysis_type": "maintenance_strategy",
            "summary": "ok",
            "structured_report": {},
            "evidence": {"used_rag": True, "rag_sources": [], "nl2sql_calls": [], "data_coverage": {}},
            "trace": {"plan_id": "p2", "node_latency_ms": {}, "template_versions": {}},
        }
        req = AnalysisNL2SQLRequest(
            user_id="user_api_nl2sql",
            session_id="sess_api_nl2sql",
            analysis_type="maintenance_strategy",
            query="测试 nl2sql 路由",
            data_requirements_hint=["壁厚"],
        )
        with patch.object(
            analysis_api.service,
            "run_analysis_nl2sql",
            new=AsyncMock(return_value=fake_result),
        ) as mock_call:
            body = asyncio.run(analysis_api.run_analysis_with_nl2sql(req))
        self.assertEqual("maintenance_strategy", body["analysis_type"])
        mock_call.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
