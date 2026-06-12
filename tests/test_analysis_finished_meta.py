"""综合分析流式尾帧 meta 与 AI 问答对齐。"""

from __future__ import annotations

import json
import unittest
from time import perf_counter

from app.llm.graphs.analysis_finished_meta import (
    analysis_finished_sse_event,
    build_analysis_finished_meta,
)
from app.services.analysis_service import _encode_sse_event


class TestAnalysisFinishedMeta(unittest.TestCase):
    def test_finished_sse_envelope_matches_chatbot(self):
        t0 = perf_counter()
        meta = build_analysis_finished_meta(
            request_id="anl_test123",
            plan_id="plan_1",
            analysis_type="overheat_guidance",
            data_mode="nl2sql",
            used_rag=True,
            used_plan_rag=True,
            used_business_rag=True,
            rag_citations=[{"namespace": "Power_plant_knowledge", "doc_name": "规程"}],
            start_ts=t0,
            synthesis_strategy_effective="v2",
            synthesis_ms=1200,
            used_nl2sql=True,
            nl2sql_sql="SELECT 1",
        )
        ev = analysis_finished_sse_event(meta)
        self.assertTrue(ev.get("finished"))
        self.assertNotIn("event", ev)
        self.assertIn("meta", ev)

        m = ev["meta"]
        self.assertEqual("anl_test123", m["stream_id"])
        self.assertEqual("anl_test123", m["request_id"])
        self.assertEqual("analysis_overheat_guidance", m["intent_label"])
        self.assertTrue(m["used_nl2sql"])
        self.assertEqual("SELECT 1", m["nl2sql_sql"])
        self.assertEqual("v2", m["synthesis_strategy_effective"])
        self.assertEqual(1200, m["synthesis_ms"])
        self.assertEqual([], m["suggested_questions"])
        self.assertEqual(1, len(m["rag_citations"]))

    def test_encode_sse_finished_frame(self):
        meta = build_analysis_finished_meta(
            request_id="anl_x",
            plan_id="p",
            analysis_type="overheat_guidance",
            data_mode="nl2sql",
            used_rag=False,
            used_plan_rag=False,
            used_business_rag=False,
            rag_citations=[],
            start_ts=perf_counter(),
        )
        raw = _encode_sse_event(analysis_finished_sse_event(meta)).decode("utf-8")
        payload = json.loads(raw[6:].strip())
        self.assertTrue(payload["finished"])
        self.assertEqual("anl_x", payload["meta"]["request_id"])


if __name__ == "__main__":
    unittest.main()
