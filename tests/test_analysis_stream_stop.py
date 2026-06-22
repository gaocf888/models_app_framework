"""综合分析流式中断（与智能客服 stop 同语义）。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.llm.graphs.analysis_finished_meta import build_analysis_finished_meta
from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.llm.graphs.analysis_synthesis_v2 import (
    AnalysisSynthesisV2Engine,
    SynthesisV2RunResult,
    SynthesisV2SlotOutput,
)
from app.services.analysis_stream_control import AnalysisStreamControl


class TestAnalysisStreamControl(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_stream_sets_flag(self) -> None:
        ctrl = AnalysisStreamControl()
        sid = ctrl.begin_stream("u1", "s1")
        self.assertFalse(await ctrl.is_cancelled("u1", "s1", sid))
        await ctrl.cancel_stream("u1", "s1", sid)
        self.assertTrue(await ctrl.is_cancelled("u1", "s1", sid))
        await ctrl.clear_stream("u1", "s1", sid)
        self.assertFalse(await ctrl.is_cancelled("u1", "s1", sid))


class TestAnalysisFinishedMetaAborted(unittest.TestCase):
    def test_aborted_meta_fields(self) -> None:
        meta = build_analysis_finished_meta(
            request_id="anl_test",
            plan_id="plan_test",
            analysis_type="overheat_guidance",
            data_mode="nl2sql",
            used_rag=True,
            used_plan_rag=False,
            used_business_rag=True,
            rag_citations=[],
            start_ts=0.0,
            stream_id="abc123",
            status="aborted",
            terminate_reason="user_cancelled",
            is_partial=True,
        )
        self.assertEqual("aborted", meta["status"])
        self.assertEqual("user_cancelled", meta["terminate_reason"])
        self.assertTrue(meta["is_partial"])
        self.assertEqual("abc123", meta["stream_id"])


class TestSynthesisV2StreamCancel(unittest.IsolatedAsyncioTestCase):
    async def test_partial_cancelled_omits_unstarted_slots(self) -> None:
        engine = AnalysisSynthesisV2Engine(
            llm_client=MagicMock(),
            prompts=MagicMock(),
            gathered_json_max_chars=1000,
            segment_max_tokens=256,
            max_parallel_llm=2,
            table_max_rows=10,
            synthesis_timeout_seconds=30.0,
            emit_structured_sse=False,
        )
        outputs: list[SynthesisV2SlotOutput | None] = [
            SynthesisV2SlotOutput("s1", "static_markdown", "", "# Title\n\n"),
            None,
            None,
        ]
        bg_tasks = [
            asyncio.create_task(asyncio.sleep(60)),
            asyncio.create_task(asyncio.sleep(60)),
        ]
        result = await engine._partial_cancelled_result(outputs, bg_tasks, analysis_type="overheat_guidance")
        self.assertTrue(result.user_cancelled)
        self.assertIn("# Title", result.summary)
        self.assertEqual(1, len(result.slot_trace))


class TestAnalysisGraphRunnerCancelChecker(unittest.IsolatedAsyncioTestCase):
    async def test_build_stream_cancel_checker(self) -> None:
        ctrl = AnalysisStreamControl()
        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=MagicMock(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
            stream_control=ctrl,
        )
        sid = ctrl.begin_stream("u1", "s1")
        check = runner._build_stream_cancel_checker("u1", "s1", sid)
        assert check is not None
        self.assertFalse(await check())
        await ctrl.cancel_stream("u1", "s1", sid)
        self.assertTrue(await check())


if __name__ == "__main__":
    unittest.main()
