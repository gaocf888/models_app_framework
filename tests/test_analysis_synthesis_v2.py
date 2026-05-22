import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.llm.graphs.analysis_synthesis_v2 import (
    get_synthesis_v2_slots,
    render_markdown_table,
    synthesis_v2_registry_available,
)
from app.models.analysis import AnalysisNL2SQLRequest, AnalysisOptions


class TestSynthesisV2Registry(unittest.TestCase):
    def test_overheat_registry_exists(self):
        self.assertTrue(synthesis_v2_registry_available("overheat_guidance"))
        self.assertGreater(len(get_synthesis_v2_slots("overheat_guidance")), 5)

    def test_unknown_type_no_registry(self):
        self.assertFalse(synthesis_v2_registry_available("unknown_type"))


class TestRenderMarkdownTable(unittest.TestCase):
    def test_table_truncation_note(self):
        rows = [{"a": i, "b": f"v{i}"} for i in range(100)]
        md, tbl = render_markdown_table(rows, max_rows=10, title="测试表")
        self.assertIn("测试表", md)
        self.assertTrue(tbl.get("truncated"))
        self.assertEqual(10, len(tbl["rows"]))


class _FakePromptRegistry:
    @staticmethod
    def get_template(scene, user_id=None, version=None):  # noqa: ANN001
        _ = (user_id, version)
        if "narrative" in str(scene):
            return SimpleNamespace(content="叙述 system", version="v1")
        return SimpleNamespace(content="v1 synthesis", version="v1")


def _reset_template_version_cfg(cfg) -> None:  # noqa: ANN001
    """测试隔离：避免本机 .env 中专项/全局模板版本干扰断言。"""
    cfg.plan_template_version = None
    cfg.plan_template_version_overheat_guidance = None
    cfg.plan_template_version_maintenance_strategy = None
    cfg.plan_template_version_four_tube_health_interpretation = None
    cfg.plan_template_version_leakage_burst_analysis = None
    cfg.plan_template_version_custom = None
    cfg.synthesis_template_version = None
    cfg.synthesis_template_version_overheat_guidance = None
    cfg.synthesis_template_version_maintenance_strategy = None
    cfg.synthesis_template_version_four_tube_health_interpretation = None
    cfg.synthesis_template_version_leakage_burst_analysis = None
    cfg.synthesis_template_version_custom = None


class TestAnalysisSynthesisStrategy(unittest.TestCase):
    def test_default_v1_without_env(self):
        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_strategy = "v1"
        runner._analysis_cfg.synthesis_strategy_overheat_guidance = None
        _reset_template_version_cfg(runner._analysis_cfg)
        self.assertEqual("v1", runner._configured_synthesis_strategy("overheat_guidance"))
        eff, fb = runner._resolve_synthesis_strategy_effective("overheat_guidance")
        self.assertEqual("v1", eff)
        self.assertIsNone(fb)

    def test_v2_fallback_when_no_registry(self):
        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_strategy = "v2"
        eff, fb = runner._resolve_synthesis_strategy_effective("maintenance_strategy")
        self.assertEqual("v1", eff)
        self.assertEqual("v2_registry_missing", fb)

    def test_plan_template_version_only_when_v2(self):
        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_strategy = "v1"
        runner._analysis_cfg.synthesis_strategy_overheat_guidance = None
        _reset_template_version_cfg(runner._analysis_cfg)
        self.assertIsNone(runner._resolve_plan_template_version("overheat_guidance"))
        runner._analysis_cfg.synthesis_strategy_overheat_guidance = "v2"
        self.assertEqual("v2", runner._resolve_plan_template_version("overheat_guidance"))

    def test_per_type_plan_version_overrides_global(self):
        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_strategy = "v1"
        runner._analysis_cfg.plan_template_version = "v2"
        runner._analysis_cfg.plan_template_version_overheat_guidance = "v1"
        self.assertEqual("v1", runner._resolve_plan_template_version("overheat_guidance"))
        self.assertEqual("v2", runner._resolve_plan_template_version("maintenance_strategy"))

    def test_per_type_synthesis_template_on_v1_strategy(self):
        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_strategy = "v1"
        runner._analysis_cfg.synthesis_template_version_overheat_guidance = "v1"
        self.assertEqual("v1", runner._resolve_synthesis_template_version("overheat_guidance"))
        self.assertIsNone(runner._resolve_synthesis_template_version("maintenance_strategy"))

    def test_synthesis_stage_template_uses_env_not_user_hash(self):
        """专项 v1 须传给 get_template(version='v1')，避免 u_web_overheat_stream 哈希到 v2。"""
        recorded: dict[str, object] = {}

        class _RecordingRegistry:
            def get_template(self, scene, user_id=None, version=None):  # noqa: ANN001
                recorded["scene"] = scene
                recorded["version"] = version
                recorded["user_id"] = user_id
                if version == "v1":
                    return SimpleNamespace(content="六章 v1 正文", version="v1")
                return SimpleNamespace(content="九章 v2 正文", version="v2")

        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=_RecordingRegistry(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_strategy = "v1"
        runner._analysis_cfg.synthesis_template_version = "v2"
        runner._analysis_cfg.synthesis_template_version_overheat_guidance = "v1"
        text, ver = runner._resolve_synthesis_stage_template(
            analysis_type="overheat_guidance",
            user_id="u_web_overheat_stream",
            default_text="default",
        )
        self.assertEqual("v1", recorded["version"])
        self.assertEqual("analysis_synthesis_overheat_guidance", recorded["scene"])
        self.assertEqual("六章 v1 正文", text)
        self.assertIn("v1", ver)

    def test_execute_synthesis_v1_uses_chat(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value="v1 结论")
        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=llm,
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        out = asyncio.run(
            runner._execute_synthesis(
                query="测试",
                analysis_type="overheat_guidance",
                data_mode="nl2sql",
                data_blob={"q1": [{"a": 1}]},
                context_snippets=[],
                system_prompt="sys",
                user_id="u1",
            )
        )
        self.assertEqual("v1", out.strategy_effective)
        self.assertEqual("v1 结论", out.summary)
        llm.chat.assert_awaited_once()

    def test_execute_synthesis_v2_mock_slots(self):
        llm = MagicMock()
        llm.chat = AsyncMock(return_value="章节正文")
        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=llm,
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_strategy_overheat_guidance = "v2"
        runner._analysis_cfg.synthesis_v2_max_parallel_llm = 2
        out = asyncio.run(
            runner._execute_synthesis(
                query="超温分析",
                analysis_type="overheat_guidance",
                data_mode="nl2sql",
                data_blob={
                    "q1": [{"device_name": "屏过", "highest_temp": 580}],
                    "q2": [{"time": "2026-01-01", "temperature": 570}],
                    "q3": [{"defect": "减薄"}],
                },
                context_snippets=["规则片段"],
                system_prompt="ignored",
                user_id="u_v2",
                chart_mode="auto",
            )
        )
        self.assertEqual("v2", out.strategy_effective)
        self.assertIn("章节正文", out.summary)
        self.assertGreater(llm.chat.await_count, 1)
        self.assertTrue(any(t.get("title") for t in out.v2_tables) or out.v2_tables == [])


class TestBuildStructuredReportV2Merge(unittest.TestCase):
    def test_v2_tables_prepended(self):
        report = AnalysisGraphRunner._build_structured_report(
            summary="全文",
            suggestions=[],
            analysis_type="overheat_guidance",
            report_style="standard",
            report_template="standard",
            chart_mode="off",
            data_coverage={"records": []},
            v2_tables=[{"title": "程序表", "rows": []}],
            v2_sections=[{"title": "一、基础", "content": "x"}],
            synthesis_strategy_effective="v2",
        )
        self.assertEqual("v2", report["meta"]["synthesis_strategy_effective"])
        self.assertEqual("程序表", report["tables"][0]["title"])
        self.assertEqual("一、基础", report["sections"][0]["title"])


if __name__ == "__main__":
    unittest.main()
