"""缺陷识别看图诊断编排层集成测试（mock 视觉/NL2SQL/RAG 臂）。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.llm.graphs.analysis_img_diag_runner import AnalysisImgDiagGraphRunner
from app.models.analysis import AnalysisImgDiagRequest, AnalysisNL2SQLCall, AnalysisOptions


class _FakePromptRegistry:
    @staticmethod
    def get_template(scene, user_id=None, version=None):  # noqa: ANN001
        _ = (scene, user_id, version)
        return SimpleNamespace(content="测试模板", version="test_v1")


def _make_runner(*, rag_mode: str = "vision_augmented", subtype: str = "defect_ident") -> AnalysisImgDiagGraphRunner:
    runner = AnalysisImgDiagGraphRunner(
        conv_manager=MagicMock(),
        llm_client=MagicMock(),
        prompt_registry=_FakePromptRegistry(),
        hybrid_rag=MagicMock(),
        nl2sql_service=MagicMock(),
    )
    runner._analysis_cfg.img_diag_rag_mode = rag_mode
    runner._analysis_cfg.img_diag_lane_timeout_seconds = 30.0
    runner._analysis_cfg.nl2sql_llm_planner_enabled = False
    return runner


def _sample_req(*, subtype: str = "defect_ident", enable_rag: bool = True) -> AnalysisImgDiagRequest:
    images = ["http://example.com/a.jpg"] if subtype == "defect_ident" else []
    return AnalysisImgDiagRequest(
        user_id="u1",
        session_id="s1",
        img_diag_subtype=subtype,  # type: ignore[arg-type]
        query=(
            "#2炉高温过热器B侧第4排于2025-03-01 14:00发生泄爆，请分析原因"
            if subtype == "leakage_burst"
            else "1号炉低温过热器第2排缺陷识别"
        ),
        image_urls=images,
        options=AnalysisOptions(enable_rag=enable_rag),
    )


def _sample_nl_state(*, with_intent: bool = True) -> dict:
    intent = {
        "time_window": {"start": "2025-01-01", "end": "2025-06-01"},
        "time_window_tag": "近半年",
        "scope": {"boiler": "1号锅炉", "device_name": "低温过热器", "row_no": 2},
    }
    call = AnalysisNL2SQLCall(
        item_id="q1",
        purpose="管段基础参数",
        question="test",
        sql="SELECT 1",
        status="success",
        row_count=1,
        attempts=1,
        question_intent=intent if with_intent else None,
    )
    return {
        "nl2sql_calls": [call.model_dump(mode="json")],
        "gathered_data": {"q1": [{"锅炉名称": "1号锅炉"}]},
        "plan_tasks": [{"item_id": "q1"}],
        "plan_context": [],
        "plan_rag_sources": [],
        "plan_rag_chunks": [],
        "quality_report": {"warnings": []},
        "task_status": {},
        "node_latency_ms": {},
        "planner_warnings": [],
        "request_id": "anl_test001",
        "plan_id": "plan_test001",
    }


class TestImgDiagDefectIdentOrchestration(unittest.TestCase):
    def test_split_parsed_intent_snapshot(self) -> None:
        intent = {
            "time_window": {"start": "2025-01-01"},
            "time_window_tag": "近半年",
            "scope": {"boiler": "1号炉", "row_no": 1},
            "scope_question": "1号炉",
        }
        time_part, scope_part = AnalysisImgDiagGraphRunner.split_parsed_intent_snapshot(intent)
        self.assertEqual(time_part["time_window_tag"], "近半年")
        self.assertEqual(scope_part["boiler"], "1号炉")
        self.assertEqual(scope_part["row_no"], 1)

    def test_gather_vision_augmented_topology(self) -> None:
        runner = _make_runner(rag_mode="vision_augmented")
        req = _sample_req()
        vision = {"defect_type": "磨损", "risk_level": "moderate"}
        nl_state = _sample_nl_state()

        with (
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=(vision, 100))),
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)),
            patch.object(
                runner,
                "_lane_business_rag",
                new=AsyncMock(return_value=(["aug snippet"], [{"doc_name": "d1"}], [], 50, "success", "aug-q")),
            ),
            patch.object(
                runner,
                "_lane_business_rag_prefetch",
                new=AsyncMock(return_value=([], [], [], 0, "skipped", "")),
            ),
        ):
            pack = asyncio.run(runner._gather_img_diag_pack(req))

        self.assertEqual("vision_nl_parallel_then_serial_rag", pack.parallel_trace["orchestrator_topology"])
        self.assertTrue(pack.parallel_trace["rag_depends_on_vision"])
        self.assertIn("aug snippet", pack.biz_snippets)
        self.assertEqual("1号锅炉", pack.parsed_scope_intent.get("boiler"))
        self.assertEqual("近半年", pack.parsed_time_intent.get("time_window_tag"))

    def test_gather_hybrid_merges_prefetch_and_augmented(self) -> None:
        runner = _make_runner(rag_mode="hybrid")
        req = AnalysisImgDiagRequest(
            user_id="u1",
            session_id="s1",
            query="缺陷识别 hybrid 模式",
            image_urls=["http://example.com/a.jpg"],
            options=AnalysisOptions(enable_rag=True),
        )
        vision = {"defect_type": "点蚀"}
        nl_state = _sample_nl_state(with_intent=False)

        with (
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=(vision, 80))),
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)),
            patch.object(
                runner,
                "_lane_business_rag_prefetch",
                new=AsyncMock(
                    return_value=(["prefetch snippet"], [{"doc_name": "p1"}], [], 40, "success", "pf-q")
                ),
            ),
            patch.object(
                runner,
                "_lane_business_rag",
                new=AsyncMock(
                    return_value=(["aug snippet"], [{"doc_name": "a1"}], [], 60, "success", "aug-q")
                ),
            ),
        ):
            pack = asyncio.run(runner._gather_img_diag_pack(req))

        self.assertEqual("vision_nl_parallel_prefetch_then_serial_rag", pack.parallel_trace["orchestrator_topology"])
        self.assertEqual(2, len(pack.biz_snippets))
        self.assertIn("prefetch snippet", pack.biz_snippets)
        self.assertIn("aug snippet", pack.biz_snippets)

    def test_gather_parallel_skips_augmented_rag(self) -> None:
        runner = _make_runner(rag_mode="parallel")
        req = AnalysisImgDiagRequest(
            user_id="u1",
            session_id="s1",
            query="parallel 模式",
            image_urls=["http://example.com/a.jpg"],
            options=AnalysisOptions(enable_rag=True),
        )

        with (
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=({}, 10))),
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=_sample_nl_state())),
            patch.object(
                runner,
                "_lane_business_rag_prefetch",
                new=AsyncMock(
                    return_value=(["only prefetch"], [{"doc_name": "p1"}], [], 30, "success", "pf-only")
                ),
            ),
            patch.object(runner, "_lane_business_rag", new=AsyncMock()) as mock_aug,
        ):
            pack = asyncio.run(runner._gather_img_diag_pack(req))

        mock_aug.assert_not_called()
        self.assertEqual("vision_nl_rag_parallel", pack.parallel_trace["orchestrator_topology"])
        self.assertEqual(["only prefetch"], pack.biz_snippets)

    def test_strict_nl2sql_value_error_propagates(self) -> None:
        runner = _make_runner()
        req = AnalysisImgDiagRequest(
            user_id="u1",
            session_id="s1",
            query="strict 阻断",
            image_urls=["http://example.com/a.jpg"],
            options=AnalysisOptions(enable_rag=False, strict=True),
        )

        with (
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=({}, 10))),
            patch.object(
                runner,
                "_lane_nl2sql_until_gate",
                new=AsyncMock(side_effect=ValueError("scope parse blocked")),
            ),
        ):
            with self.assertRaises(ValueError):
                asyncio.run(runner._gather_img_diag_pack(req))

    def test_run_with_img_diag_finalize_trace_fields(self) -> None:
        runner = _make_runner()
        req = AnalysisImgDiagRequest(
            user_id="u1",
            session_id="s1",
            query="完整链路",
            image_urls=["http://example.com/a.jpg"],
            options=AnalysisOptions(enable_rag=False),
        )
        nl_state = _sample_nl_state()

        with (
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=({"defect_type": "裂纹"}, 20))),
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)),
            patch.object(runner, "_generate_summary", new=AsyncMock(return_value="测试结论摘要。")),
        ):
            result = asyncio.run(runner.run_with_img_diag(req))

        self.assertEqual("img_diag_defect_ident", result.analysis_type)
        cov = result.evidence.data_coverage
        self.assertEqual("img_diag_defect_ident", cov["mode"])
        self.assertIn("parsed_time_intent", cov)
        self.assertIn("parsed_scope_intent", cov)
        self.assertEqual("近半年", cov["parsed_time_intent"]["time_window_tag"])
        self.assertEqual("1号锅炉", cov["parsed_scope_intent"]["boiler"])
        self.assertIn("parsed_scope_intent", result.trace.execution_summary)

    def test_leakage_burst_no_image_skips_vision(self) -> None:
        runner = _make_runner()
        req = _sample_req(subtype="leakage_burst", enable_rag=False)
        nl_state = _sample_nl_state()

        with patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)):
            pack = asyncio.run(runner._gather_img_diag_pack(req))

        self.assertEqual("skipped", pack.parallel_trace["vision_lane_status"])
        self.assertTrue(pack.vision_data.get("vision_skipped"))
        self.assertEqual("img_diag_leakage_burst", pack.profile.analysis_type)

    def test_run_leakage_burst_analysis_type(self) -> None:
        runner = _make_runner()
        req = _sample_req(subtype="leakage_burst", enable_rag=False)
        nl_state = _sample_nl_state()

        with (
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)),
            patch.object(runner, "_generate_summary", new=AsyncMock(return_value="泄爆溯源结论。")),
        ):
            result = asyncio.run(runner.run_with_img_diag(req))

        self.assertEqual("img_diag_leakage_burst", result.analysis_type)
        self.assertEqual("img_diag_leakage_burst", result.evidence.data_coverage["mode"])
        self.assertEqual("leakage_burst", result.evidence.data_coverage["img_diag_subtype"])

    def test_gather_pack_builds_rag_citations_from_business_rag(self) -> None:
        from app.rag.models import RetrievedChunk

        runner = _make_runner()
        req = _sample_req(enable_rag=True)
        nl_state = _sample_nl_state()
        chunk = RetrievedChunk(
            text="打磨补焊工艺要点",
            doc_name="缺陷处置规程.docx",
            namespace="Power_plant_knowledge",
            chunk_id="c_diag_1",
            score=0.88,
            metadata={"content_fetched_from_url": "https://cdn.example.com/defect_guide.docx"},
        )

        async def fake_stream(**_kwargs: object):
            yield "流式结论"

        with (
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=({"defect_type": "裂纹"}, 10))),
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)),
            patch.object(
                runner,
                "_lane_business_rag",
                new=AsyncMock(
                    return_value=(
                        ["打磨补焊工艺要点"],
                        [{"namespace": "Power_plant_knowledge", "doc_id": "d1"}],
                        [chunk],
                        12,
                        "success",
                        "缺陷识别 RAG query",
                    )
                ),
            ),
            patch.object(runner, "_stream_summary_text", new=fake_stream),
            patch(
                "app.llm.graphs.analysis_img_diag_runner.dispatch_analysis_nl2sql_stream_structured",
                new=AsyncMock(),
            ),
        ):
            events = asyncio.run(self._collect_img_diag_stream_events(runner, req))

        finished = [e for e in events if e.get("finished")]
        self.assertEqual(1, len(finished))
        meta = finished[0]["meta"]
        self.assertTrue(meta.get("used_rag"))
        self.assertTrue(meta.get("used_business_rag"))
        cites = meta.get("rag_citations") or []
        self.assertEqual(1, len(cites))
        self.assertEqual(1, cites[0].get("ref_index"))
        self.assertEqual("缺陷处置规程.docx", cites[0].get("doc_name"))
        self.assertEqual("Power_plant_knowledge", cites[0].get("namespace"))
        self.assertEqual(
            "https://cdn.example.com/defect_guide.docx",
            cites[0].get("original_content_url"),
        )

    def test_sync_result_evidence_rag_citations(self) -> None:
        from app.rag.models import RetrievedChunk

        runner = _make_runner()
        req = _sample_req(enable_rag=True)
        nl_state = _sample_nl_state()
        chunk = RetrievedChunk(
            text="同类爆管案例",
            doc_name="leakage_case.pdf",
            namespace="global",
            chunk_id="c_leak_1",
        )

        with (
            patch.object(runner, "_lane_vision", new=AsyncMock(return_value=({"burst_type": "环向开口"}, 10))),
            patch.object(runner, "_lane_nl2sql_until_gate", new=AsyncMock(return_value=nl_state)),
            patch.object(
                runner,
                "_lane_business_rag",
                new=AsyncMock(
                    return_value=(["同类爆管案例"], [], [chunk], 8, "success", "泄爆 RAG")
                ),
            ),
            patch.object(runner, "_generate_summary", new=AsyncMock(return_value="泄爆分析结论。")),
        ):
            result = asyncio.run(runner.run_with_img_diag(req))

        self.assertEqual(1, len(result.evidence.rag_citations or []))
        self.assertEqual("leakage_case.pdf", result.evidence.rag_citations[0]["doc_name"])
        runner._conv.append_assistant_message.assert_called_once()
        _args, kwargs = runner._conv.append_assistant_message.call_args
        self.assertEqual(1, len(kwargs.get("rag_citations") or []))

    @staticmethod
    async def _collect_img_diag_stream_events(
        runner: AnalysisImgDiagGraphRunner, req: AnalysisImgDiagRequest
    ) -> list[dict]:
        out: list[dict] = []
        async for ev in runner.iter_img_diag_stream_events(req):
            out.append(ev)
        return out


if __name__ == "__main__":
    unittest.main()
