import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.models.analysis import AnalysisNL2SQLRequest, AnalysisOptions, AnalysisPayloadRequest

_INTENT_JSON = '{"goals":["测试目标"],"key_entities":[],"time_scope_hint":"","output_focus":[],"data_domains":[]}'
_PLAN_EMPTY_JSON = '{"tasks":[]}'


class _FakePromptRegistry:
    @staticmethod
    def get_template(scene, user_id=None, version=None):  # noqa: ANN001
        _ = (scene, user_id, version)
        if str(scene).startswith("analysis_plan_"):
            return None
        return SimpleNamespace(content="你是测试分析助手。", version="test_v1")


class _FakeHybridRAG:
    @staticmethod
    def retrieve(query, namespace=None, top_k=None):  # noqa: ANN001
        _ = (query, namespace, top_k)
        return ["测试知识片段A", "测试知识片段B"]


class TestAnalysisPhase1Runner(unittest.TestCase):
    def test_payload_mode_returns_structured_result(self):
        conv = MagicMock()
        llm = MagicMock()
        llm.generate = AsyncMock(return_value="这是 payload 模式测试结论。")
        runner = AnalysisGraphRunner(
            conv_manager=conv,
            llm_client=llm,
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=_FakeHybridRAG(),
            nl2sql_service=MagicMock(),
        )
        req = AnalysisPayloadRequest(
            user_id="user_payload",
            session_id="sess_payload",
            analysis_type="overheat_guidance",
            query="请给出超温原因",
            payload={"sensor_points": [{"p": 1}]},
        )

        result = asyncio.run(runner.run_with_payload(req))
        self.assertEqual("overheat_guidance", result.analysis_type)
        self.assertTrue(result.evidence.used_rag)
        self.assertIn("sections", result.structured_report)
        self.assertIn("suggestions", result.structured_report)
        self.assertIn("meta", result.structured_report)
        self.assertIn("charts", result.structured_report)
        self.assertGreaterEqual(len(result.summary), 1)
        self.assertIn("data_quality_report", result.evidence.data_coverage)
        self.assertIn("completeness", result.evidence.data_coverage["data_quality_report"])
        self.assertIn("threshold_result", result.evidence.data_coverage["data_quality_report"])
        self.assertIn("test_v1", result.trace.template_versions["synthesis"])
        self.assertIn("test_v1", result.trace.template_versions["report"])
        self.assertIn("execution_summary", result.trace.model_dump())
        self.assertIn("node_status", result.trace.model_dump())
        self.assertIn("graph_nodes", result.trace.execution_summary)
        self.assertGreaterEqual(len(result.evidence.rag_sources), 1)
        self.assertIn("namespace", result.evidence.rag_sources[0])
        self.assertGreaterEqual(len(result.structured_report["suggestions"]), 1)
        self.assertIn("category", result.structured_report["suggestions"][0])
        self.assertIn("owner", result.structured_report["suggestions"][0])
        conv.append_user_message.assert_called_once()
        conv.append_assistant_message.assert_called_once()

    def test_nl2sql_mode_records_calls(self):
        conv = MagicMock()
        llm = MagicMock()
        llm.generate = AsyncMock(
            side_effect=[_INTENT_JSON, _PLAN_EMPTY_JSON, "这是 nl2sql 模式测试结论。"]
        )
        nl2sql = MagicMock()
        nl2sql.query = AsyncMock(
            return_value=SimpleNamespace(
                sql="select 1 as x",
                rows=[{"x": 1}, {"x": 2}],
            )
        )
        runner = AnalysisGraphRunner(
            conv_manager=conv,
            llm_client=llm,
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=_FakeHybridRAG(),
            nl2sql_service=nl2sql,
        )
        req = AnalysisNL2SQLRequest(
            user_id="user_nl2sql",
            session_id="sess_nl2sql",
            analysis_type="maintenance_strategy",
            query="请给出检修分级建议",
            data_requirements_hint=["换管记录", "壁厚测量"],
        )

        result = asyncio.run(runner.run_with_nl2sql(req))
        self.assertEqual("maintenance_strategy", result.analysis_type)
        self.assertEqual(5, len(result.evidence.nl2sql_calls))
        self.assertEqual(5, result.evidence.data_coverage.get("success_calls"))
        self.assertEqual(0, result.evidence.data_coverage.get("failed_calls"))
        self.assertIn("data_quality_report", result.evidence.data_coverage)
        self.assertEqual(1.0, result.evidence.data_coverage["data_quality_report"]["completeness"])
        self.assertIn("threshold_result", result.evidence.data_coverage["data_quality_report"])
        self.assertIn("test_v1", result.trace.template_versions["intent"])
        self.assertTrue(all(c.attempts >= 1 for c in result.evidence.nl2sql_calls))
        self.assertIn("data_plan_trace", result.trace.model_dump())
        self.assertIn("node_latency_ms", result.trace.model_dump())
        self.assertGreaterEqual(len(result.evidence.rag_sources), 1)
        self.assertIn("graph_nodes", result.trace.execution_summary)
        self.assertEqual(5, nl2sql.query.await_count)
        self.assertEqual(3, llm.generate.await_count)

    def test_nl2sql_dependency_failure_triggers_skip(self):
        conv = MagicMock()
        llm = MagicMock()
        llm.generate = AsyncMock(
            side_effect=[_INTENT_JSON, _PLAN_EMPTY_JSON, "这是依赖失败测试结论。"]
        )
        nl2sql = MagicMock()
        # 所有 NL2SQL 调用都失败，验证依赖项被 skipped
        nl2sql.query = AsyncMock(side_effect=RuntimeError("db temporary error"))
        runner = AnalysisGraphRunner(
            conv_manager=conv,
            llm_client=llm,
            prompt_registry=_FakePromptRegistry(),
            hybrid_rag=_FakeHybridRAG(),
            nl2sql_service=nl2sql,
        )
        req = AnalysisNL2SQLRequest(
            user_id="user_nl2sql_dep",
            session_id="sess_nl2sql_dep",
            analysis_type="maintenance_strategy",
            query="请给出检修分级建议",
        )

        result = asyncio.run(runner.run_with_nl2sql(req))
        statuses = [c.status for c in result.evidence.nl2sql_calls]
        self.assertIn("failed", statuses)
        self.assertIn("skipped", statuses)
        quality = result.evidence.data_coverage["data_quality_report"]
        self.assertGreaterEqual(quality["mandatory_failed"], 1)
        self.assertIn("mandatory_steps_failed", result.trace.degrade_reasons)
        self.assertEqual(3, llm.generate.await_count)

    def test_data_plan_template_extension_without_runner_change(self):
        class _TemplatePromptRegistry:
            @staticmethod
            def get_template(scene, user_id=None, version=None):  # noqa: ANN001
                _ = (user_id, version)
                if scene == "analysis_plan_custom":
                    return SimpleNamespace(
                        content=(
                            '[{"item_id":"q1","purpose":"自定义数据A","question":"查询自定义A","mandatory":true},'
                            '{"item_id":"q2","purpose":"自定义数据B","question":"查询自定义B","mandatory":false,"dependency_ids":["q1"]}]'
                        ),
                        version="plan_v1",
                    )
                return SimpleNamespace(content="你是测试分析助手。", version="test_v1")

        runner = AnalysisGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=_TemplatePromptRegistry(),
            hybrid_rag=_FakeHybridRAG(),
            nl2sql_service=MagicMock(),
        )
        req = AnalysisNL2SQLRequest(
            user_id="user_custom",
            session_id="sess_custom",
            analysis_type="custom",
            query="请按自定义模板取数",
            data_requirements_hint=["补充字段X"],
        )
        tasks = runner._build_data_plan(req, plan_context=[])
        self.assertGreaterEqual(len(tasks), 3)
        self.assertEqual("q1", tasks[0].item_id)
        self.assertEqual("q2", tasks[1].item_id)
        self.assertEqual(["q1"], tasks[1].dependency_ids)


if __name__ == "__main__":
    unittest.main()
