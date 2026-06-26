"""综合分析流式中断（与智能客服 stop 同语义）。"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestImgDiagStreamCancel(unittest.IsolatedAsyncioTestCase):
    async def test_iter_img_diag_stream_emits_started_first(self) -> None:
        from app.llm.graphs.analysis_img_diag_runner import AnalysisImgDiagGraphRunner
        from app.models.analysis import AnalysisImgDiagRequest, AnalysisOptions
        from app.services.analysis_stream_control import AnalysisStreamControl

        ctrl = AnalysisStreamControl()
        runner = AnalysisImgDiagGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=MagicMock(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
            stream_control=ctrl,
        )
        runner._analysis_cfg.img_diag_lane_timeout_seconds = 30.0
        runner._analysis_cfg.nl2sql_llm_planner_enabled = False

        req = AnalysisImgDiagRequest(
            user_id="u1",
            session_id="s1",
            img_diag_subtype="defect_ident",
            query="1号炉低温过热器第2排缺陷识别",
            image_urls=["http://example.com/a.jpg"],
            options=AnalysisOptions(enable_rag=False),
        )
        nl_state = {
            "nl2sql_calls": [],
            "gathered_data": {},
            "plan_tasks": [],
            "plan_context": [],
            "plan_rag_sources": [],
            "quality_report": {"warnings": []},
            "task_status": {},
            "node_latency_ms": {},
            "planner_warnings": [],
            "request_id": "anl_test001",
            "plan_id": "plan_test001",
        }

        async def fake_stream(**_kwargs: object):
            yield "部分报告"

        with (
            patch.object(
                runner,
                "_run_scope_hitl_phase",
                new=AsyncMock(return_value={"status": "skipped", "request_id": "anl_test001"}),
            ),
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=({"defect_type": "裂纹"}, 10))),
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)),
            patch.object(runner, "_stream_summary_text", new=fake_stream),
        ):
            events: list[dict] = []
            async for ev in runner.iter_img_diag_stream_events(req):
                events.append(ev)

        self.assertEqual("started", events[0].get("event"))
        self.assertTrue(events[0].get("stream_id"))
        self.assertEqual("meta", events[1].get("event"))
        finished = [e for e in events if e.get("finished")]
        self.assertEqual(1, len(finished))
        self.assertEqual(events[0]["stream_id"], finished[0]["meta"].get("stream_id"))

    async def test_iter_img_diag_stream_aborted_on_cancel(self) -> None:
        from app.llm.graphs.analysis_img_diag_runner import AnalysisImgDiagGraphRunner
        from app.models.analysis import AnalysisImgDiagRequest, AnalysisOptions
        from app.services.analysis_stream_control import AnalysisStreamControl

        ctrl = AnalysisStreamControl()
        runner = AnalysisImgDiagGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=MagicMock(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
            stream_control=ctrl,
        )
        runner._analysis_cfg.img_diag_lane_timeout_seconds = 30.0
        runner._analysis_cfg.nl2sql_llm_planner_enabled = False

        req = AnalysisImgDiagRequest(
            user_id="u1",
            session_id="s1",
            img_diag_subtype="defect_ident",
            query="1号炉低温过热器第2排缺陷识别",
            image_urls=["http://example.com/a.jpg"],
            options=AnalysisOptions(enable_rag=False),
        )
        cancel_sid: dict[str, str] = {"id": ""}

        async def fake_stream(**_kwargs: object):
            yield "首段"
            await ctrl.cancel_stream("u1", "s1", cancel_sid["id"])
            yield "不应出现"

        nl_state = {
            "nl2sql_calls": [],
            "gathered_data": {},
            "plan_tasks": [],
            "plan_context": [],
            "plan_rag_sources": [],
            "quality_report": {"warnings": []},
            "task_status": {},
            "node_latency_ms": {},
            "planner_warnings": [],
            "request_id": "anl_abort001",
            "plan_id": "plan_abort001",
        }

        with (
            patch.object(
                runner,
                "_run_scope_hitl_phase",
                new=AsyncMock(return_value={"status": "skipped", "request_id": "anl_abort001"}),
            ),
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=({"defect_type": "裂纹"}, 10))),
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)),
            patch.object(runner, "_stream_summary_text", new=fake_stream),
        ):
            events: list[dict] = []
            async for ev in runner.iter_img_diag_stream_events(req):
                if ev.get("event") == "started" and not cancel_sid["id"]:
                    cancel_sid["id"] = str(ev.get("stream_id") or "")
                events.append(ev)

        finished = [e for e in events if e.get("finished")]
        self.assertEqual(1, len(finished))
        meta = finished[0]["meta"]
        self.assertEqual("aborted", meta.get("status"))
        self.assertEqual("user_cancelled", meta.get("terminate_reason"))
        self.assertTrue(meta.get("is_partial"))
        self.assertNotIn("structured_async_enqueued", [e.get("event") for e in events])


if __name__ == "__main__":
    unittest.main()
