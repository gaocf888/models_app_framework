import asyncio
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api import analysis as analysis_api
from app.models.analysis import (
    AnalysisTrace,
    AnalysisTraceDegradeTopNResponse,
    AnalysisTraceStatsResponse,
    AnalysisTraceTrendResponse,
    AnalysisTraceView,
)


class TestAnalysisTraceApiPhase3(unittest.TestCase):
    def test_get_trace_success(self):
        fake = AnalysisTraceView(
            request_id="anl_demo",
            analysis_type="overheat_guidance",
            summary="ok",
            data_mode="payload",
            trace=AnalysisTrace(plan_id="p1"),
            data_coverage={"mode": "payload"},
        )
        with patch.object(analysis_api.service, "get_trace", return_value=fake):
            out = asyncio.run(analysis_api.get_analysis_trace("anl_demo"))
        self.assertEqual("anl_demo", out.request_id)
        self.assertEqual("payload", out.data_mode)

    def test_get_trace_not_found(self):
        with patch.object(analysis_api.service, "get_trace", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(analysis_api.get_analysis_trace("missing"))
        self.assertEqual(404, ctx.exception.status_code)

    def test_list_trace_success(self):
        fake = AnalysisTraceView(
            request_id="anl_demo_2",
            analysis_type="maintenance_strategy",
            summary="summary text",
            data_mode="nl2sql",
            trace=AnalysisTrace(plan_id="p2", execution_summary={"started_at": "2026-04-14T00:00:00Z", "used_rag": True}),
            data_coverage={"mode": "nl2sql"},
        )
        with patch.object(analysis_api.service, "list_traces", return_value=([fake], 1)):
            out = asyncio.run(analysis_api.list_analysis_traces(limit=20, offset=0))
        self.assertTrue(out.ok)
        self.assertEqual(1, out.total)
        self.assertEqual("anl_demo_2", out.items[0].request_id)
        self.assertEqual("nl2sql", out.items[0].data_mode)

    def test_list_trace_with_filters(self):
        with patch.object(analysis_api.service, "list_traces", return_value=([], 0)) as mocked:
            out = asyncio.run(
                analysis_api.list_analysis_traces(
                    limit=10,
                    offset=5,
                    analysis_type="overheat_guidance",
                    data_mode="payload",
                    request_id_like="anl_",
                    started_from="2026-04-14T00:00:00Z",
                    started_to="2026-04-15T00:00:00Z",
                )
            )
        self.assertTrue(out.ok)
        self.assertEqual(0, out.total)
        mocked.assert_called_once()

    def test_trace_stats_success(self):
        with patch.object(
            analysis_api.service,
            "get_trace_stats",
            return_value=AnalysisTraceStatsResponse(
                ok=True,
                total=3,
                by_analysis_type={"overheat_guidance": 2, "maintenance_strategy": 1},
                by_data_mode={"payload": 1, "nl2sql": 2},
                degrade_reasons={"nl2sql_failed": 1},
            ),
        ):
            out = asyncio.run(analysis_api.get_analysis_trace_stats())
        self.assertTrue(out.ok)
        self.assertEqual(3, out.total)

    def test_trace_trend_success(self):
        with patch.object(
            analysis_api.service,
            "get_trace_trend",
            return_value=AnalysisTraceTrendResponse(ok=True, bucket="hour", points=[]),
        ):
            out = asyncio.run(analysis_api.get_analysis_trace_trend(bucket="hour"))
        self.assertTrue(out.ok)
        self.assertEqual("hour", out.bucket)

    def test_trace_degrade_topn_success(self):
        with patch.object(
            analysis_api.service,
            "get_degrade_topn",
            return_value=AnalysisTraceDegradeTopNResponse(
                ok=True,
                total_unique=2,
                items=[
                    {"reason": "nl2sql_failed", "count": 3},
                    {"reason": "rag_timeout", "count": 1},
                ],
            ),
        ):
            out = asyncio.run(analysis_api.get_analysis_trace_degrade_topn(top_n=5))
        self.assertTrue(out.ok)
        self.assertEqual(2, out.total_unique)


if __name__ == "__main__":
    unittest.main()
