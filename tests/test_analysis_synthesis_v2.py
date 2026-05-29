import asyncio
import json
import time
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.analysis_service import _encode_sse_event

from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.llm.graphs.analysis_synthesis_v2 import (
    AnalysisSynthesisV2Engine,
    SynthesisV2Slot,
    _aggregate_q2_severity_table_rows,
    _build_audit_facts,
    _build_dcs_linkage_charts,
    _extract_q2_event_summary,
    _rag_snippets_for_slot,
    _render_overheat_ch1_basic_info,
    _render_overheat_ch2_item1,
    _render_overheat_ch2_item2,
    _render_overheat_ch2_item3,
    _render_overheat_ch2_item5,
    _filter_q4b_sis_rows,
    _overheat_anomaly_level_from_q1,
    _render_template_slot,
    _sanitize_report_narrative,
    _truncate_point_list,
    _resolve_data_subset,
    _resolve_live_slot_index,
    _wrap_template_markdown,
    get_synthesis_v2_slots,
    render_markdown_table,
    strip_leading_duplicate_heading,
    synthesis_v2_registry_available,
)
from app.models.analysis import AnalysisNL2SQLRequest, AnalysisOptions


class TestSseEventJsonEncoding(unittest.TestCase):
    def test_table_payload_decimal_serializable(self):
        payload = {
            "event": "table_payload",
            "slot_id": "s01",
            "table": {"rows": [{"highest_temp": Decimal("580.5"), "limit_temp": 545}]},
        }
        raw = _encode_sse_event(payload).decode("utf-8")
        self.assertTrue(raw.startswith("data: "))
        data = json.loads(raw[6:].strip())
        self.assertEqual("580.5", data["table"]["rows"][0]["highest_temp"])


class TestSynthesisV2Registry(unittest.TestCase):
    def test_overheat_registry_slot_count(self):
        self.assertTrue(synthesis_v2_registry_available("overheat_guidance"))
        slots = get_synthesis_v2_slots("overheat_guidance")
        self.assertEqual(31, len(slots))
        self.assertEqual("q1", slots[0].source_item_ids[0])
        self.assertEqual("template_deterministic", slots[0].kind)
        self.assertEqual("overheat_ch1_basic", slots[0].template_id)
        self.assertEqual("q2a", slots[2].source_item_ids[0])
        self.assertEqual("overheat_ch2_item1", slots[2].template_id)
        by_id = {s.id: s for s in slots}
        self.assertEqual(("q3a", "q3b"), by_id["s04a"].source_item_ids)
        self.assertEqual("q3a", by_id["s03"].source_item_ids[0])
        self.assertEqual("q3b", by_id["s03b"].source_item_ids[0])
        self.assertEqual("q6a", by_id["s12"].source_item_ids[0])
        self.assertEqual("q6d", by_id["s12b"].source_item_ids[0])
        self.assertEqual("overheat_q6_dcs_linkage", by_id["s12b"].table_id)

    def test_unknown_type_no_registry(self):
        self.assertFalse(synthesis_v2_registry_available("unknown_type"))


class TestSynthesisV2NarrativeHelpers(unittest.TestCase):
    def test_strip_duplicate_chapter_heading(self):
        raw = "### 一、报告基础信息\n\n机组 A\n"
        out = strip_leading_duplicate_heading(raw, "一、报告基础信息")
        self.assertEqual("机组 A", out)

    def test_strict_subset_no_full_fallback(self):
        data = {"q1": [{"a": 1}], "q2": [{"b": 2}]}
        sub = _resolve_data_subset(data, ("q1",), strict=True)
        self.assertEqual({"q1": [{"a": 1}]}, sub)
        self.assertNotIn("q2", sub)

    def test_rag_trim_when_bound_items_empty(self):
        snippets = ["规范1", "规范2", "规范3", "规范4"]
        data = {"q3": []}
        out = _rag_snippets_for_slot(snippets, data, ("q3",))
        self.assertEqual(2, len(out))

    def test_audit_facts_marks_empty_query(self):
        text = _build_audit_facts({"q3": []}, "#2机组超温")
        self.assertIn("无数据行", text)
        self.assertIn("#2机组", text)

    def test_q2_severity_aggregate_and_event_summary(self):
        rows = [
            {
                "测点编号": "P1",
                "超温等级": "严重超温",
                "测点及位置": "P1（位置：1号锅炉-末过）",
            },
            {
                "测点编号": "P2",
                "超温等级": "中度超温",
                "测点及位置": "P2（位置：1号锅炉-水冷壁）",
            },
        ]
        agg = _aggregate_q2_severity_table_rows(rows)
        self.assertEqual(2, len(agg))
        self.assertEqual(1, agg[0]["测点数量"])
        self.assertIn("P1", agg[0]["测点及位置列表"])
        summary = _extract_q2_event_summary(
            [{"全事件平均负荷_MW": 480, "全事件主汽压力_MPa": 16.2}]
        )
        self.assertEqual(480, summary["全事件平均负荷_MW"])
        audit = _build_audit_facts(
            {"q2b": [{"全事件平均负荷_MW": 480, "全事件主汽压力_MPa": 16.2}]},
            "1号锅炉",
            slot_id="s02_2",
        )
        self.assertIn("[全事件运行工况]", audit)
        self.assertIn("全事件平均负荷_MW=480", audit)

    def test_ch1_basic_info_from_q1(self):
        body = _render_overheat_ch1_basic_info(
            [
                {
                    "机组名称": "1号锅炉",
                    "锅炉型号": "HG-1000",
                    "额定负荷_MW": 600,
                    "监测部位": "水冷壁螺旋段右墙（13个测点）",
                    "超温测点总数": 98,
                    "轻微超温数量": 20,
                    "中度超温数量": 30,
                    "严重超温数量": 48,
                }
            ]
        )
        self.assertIn("1号锅炉", body)
        self.assertIn("HG-1000", body)
        self.assertIn("共98个", body)
        self.assertIn("Ⅲ级（严重超温）", body)
        self.assertNotIn("（待补充）", body)

    def test_wrap_template_markdown_with_title(self):
        md = _wrap_template_markdown("一、报告基础信息", "1.报告编号：GL-CW-001\n")
        self.assertIn("### 一、报告基础信息", md)
        self.assertIn("GL-CW-001", md)

    def test_resolve_live_slot_skips_template_s01(self):
        slots = get_synthesis_v2_slots("overheat_guidance")
        idx = _resolve_live_slot_index(slots)
        self.assertIsNotNone(idx)
        self.assertNotEqual("s01", slots[idx].id)
        self.assertEqual("llm_narrative", slots[idx].kind)

    def test_ch2_item2_pressure_kpa_to_mpa(self):
        body = _render_overheat_ch2_item2([{"全事件负荷_percent": 115.83, "全事件主汽压力_MPa": 411.17}])
        self.assertIn("0.41", body)
        self.assertIn("MPa", body)
        self.assertNotIn("主汽温度", body)

    def test_q2c_preaggregated_severity_rows(self):
        rows = [
            {"超温等级": "严重超温", "测点及位置列表": "P1（位置：1号锅炉-末过）", "测点数量": 1},
            {"超温等级": "中度超温", "测点及位置列表": "P2（位置：1号锅炉-水冷壁）", "测点数量": 1},
        ]
        item3 = _render_overheat_ch2_item3(rows)
        self.assertIn("P1（位置：1号锅炉-末过）", item3)
        self.assertIn("轻微超温（5～10℃）：无，共0个", item3)

    def test_ch2_template_renderers(self):
        rows = [
            {
                "测点编号": "P1",
                "超温等级": "严重超温",
                "测点及位置": "P1（位置：1号锅炉-末过）",
                "最早超温起始": "2026-05-01 08:00:00",
                "最晚超温结束": "2026-05-01 10:00:00",
                "超温总时长_秒": 7200,
                "时段说明": "2026-05-01 08:00:00 至 2026-05-01 10:00:00，持续 7200 秒",
                "受热面名称": "末级过热器",
            },
            {
                "测点编号": "P2",
                "超温等级": "中度超温",
                "测点及位置": "P2（位置：1号锅炉-水冷壁）",
                "最早超温起始": "2026-05-01 08:30:00",
                "最晚超温结束": "2026-05-01 09:30:00",
                "超温总时长_秒": 1800,
                "时段说明": "2026-05-01 08:30:00 至 2026-05-01 09:30:00，持续 1800 秒",
                "受热面名称": "前墙水冷壁",
            },
        ]
        item1 = _render_overheat_ch2_item1(rows)
        self.assertIn("1. 超温起止时段", item1)
        self.assertIn("2026-05-01 08:00:00", item1)
        self.assertIn("核心测点时段", item1)
        item5 = _render_overheat_ch2_item5(rows)
        self.assertIn("5. 超温分布特征", item5)
        full = _render_template_slot(
            "overheat_ch2_item2",
            [{"全事件平均负荷_MW": 480, "全事件负荷_percent": 80, "全事件主汽压力_MPa": 16.2}],
        )
        self.assertIn("负荷80%", full)
        self.assertNotIn("主汽温度", full)
        item4 = _render_template_slot(
            "overheat_ch2_item4",
            [{
                "分区域设计壁温": "过热器（末级）：540℃",
                "全事件实测最高壁温": 590,
                "全事件最高壁温测点": "P1（末过）",
                "全事件最大超温差值_监测": 25,
                "全事件最大超温差值_设计": 20,
                "全事件平均超温差值_监测": 12.5,
            }],
        )
        self.assertIn("实测最高590℃", item4)

    def test_anomaly_level_mapping(self):
        row = {"严重超温数量": 2, "中度超温数量": 0, "轻微超温数量": 0}
        self.assertEqual("Ⅲ级（严重超温）", _overheat_anomaly_level_from_q1(row))
        critical = _overheat_anomaly_level_from_q1(
            row, q2d_row={"全事件最大超温差值_监测": 45}
        )
        self.assertEqual("Ⅳ级（临界爆管风险）", critical)

    def test_sanitize_report_narrative(self):
        raw = "原因A [置信度：高]（依据：q3a 区域汇总）。见 q2a 测点。"
        out = _sanitize_report_narrative(raw)
        self.assertNotIn("置信度", out)
        self.assertNotIn("依据", out)
        self.assertNotIn("q3a", out.lower())
        self.assertNotIn("q2a", out.lower())

    def test_ch2_item1_fallback_q4a(self):
        q4a = [{"采集时间": "2026-05-01 08:00:00"}, {"采集时间": "2026-05-01 12:30:00"}]
        body = _render_overheat_ch2_item1([], gathered_data={"q4a": q4a})
        self.assertIn("2026-05-01 08:00:00", body)
        self.assertIn("壁温时序推导", body)

    def test_ch2_item5_fallback_q3a(self):
        q3a = [
            {"超温区域": "末过", "累计超温时长_秒": 5000},
            {"超温区域": "水冷壁", "累计超温时长_秒": 800},
        ]
        body = _render_overheat_ch2_item5([], gathered_data={"q3a": q3a})
        self.assertIn("集中式", body)
        self.assertIn("区域汇总推导", body)

    def test_truncate_point_list_by_entries(self):
        text = "、".join([f"P{i}（位置：1号锅炉）" for i in range(10)])
        out = _truncate_point_list(text, max_entries=3)
        self.assertIn("前3个", out)
        self.assertIn("共10个", out)
        self.assertNotIn("P9", out)

    def test_filter_q4b_excludes_wall_temp(self):
        rows = [
            {"测点编码": "10HAD11CT101", "测点名称": "末过壁温"},
            {"测点编码": "FW01", "测点名称": "减温水流量"},
        ]
        filtered = _filter_q4b_sis_rows(rows)
        self.assertEqual(1, len(filtered))
        self.assertEqual("FW01", filtered[0]["测点编码"])

    def test_dcs_linkage_charts_long_format(self):
        rows = [
            {"采集时间": "2026-05-01 10:00", "参数类型": "壁温", "参数值": 580},
            {"采集时间": "2026-05-01 10:00", "参数类型": "机组负荷", "参数值": 520},
            {"采集时间": "2026-05-01 10:05", "参数类型": "减温水", "参数值": 55},
        ]
        md, charts = _build_dcs_linkage_charts(rows, chart_mode="auto")
        self.assertEqual(3, len(charts))
        self.assertIn("DCS联动", md)
        titles = {c["title"] for c in charts}
        self.assertIn("DCS联动-壁温", titles)

    def test_dcs_linkage_charts_wide_format(self):
        rows = [
            {
                "采集时间": "2026-05-01 10:00",
                "pi_code": "T01",
                "壁温_℃": 580,
                "机组负荷_MW": 520,
                "主汽压力_MPa": 16.2,
            }
        ]
        _md, charts = _build_dcs_linkage_charts(rows, chart_mode="auto")
        self.assertGreaterEqual(len(charts), 2)

    def test_segment_user_content_includes_audit_facts(self):
        engine = AnalysisSynthesisV2Engine(
            llm_client=MagicMock(),
            prompts=_FakePromptRegistry(),
            gathered_json_max_chars=8000,
            segment_max_tokens=512,
            max_parallel_llm=1,
            table_max_rows=10,
            synthesis_timeout_seconds=30.0,
        )
        slot = get_synthesis_v2_slots("overheat_guidance")[0]
        content = engine._build_segment_user_content(
            query="#2机组",
            analysis_type="overheat_guidance",
            data_mode="nl2sql",
            gathered_data={"q1": [{"boiler_name": "1号炉", "highest_temp": 580}]},
            context_snippets=["示例 410 MPa"],
            planning_context=None,
            slot=slot,
            item_ids=slot.source_item_ids,
        )
        self.assertIn("可引用事实", content)
        self.assertIn("boiler_name=1号炉", content)
        self.assertNotIn('"q2"', content)


class TestRenderMarkdownTable(unittest.TestCase):
    def test_table_truncation_note(self):
        rows = [{"a": i, "b": f"v{i}"} for i in range(100)]
        md, tbl = render_markdown_table(rows, max_rows=10, title="测试表")
        self.assertIn("测试表", md)
        self.assertTrue(tbl.get("truncated"))
        self.assertEqual(10, len(tbl["rows"]))

    def test_table_empty_q5a_message(self):
        from app.llm.graphs.analysis_synthesis_v2 import _table_empty_message

        msg = _table_empty_message(
            "overheat_q5_defects",
            ("q5a",),
            task_status={"q5a": "success"},
            gathered_data={"q5a": []},
        )
        self.assertIn("近一年无", msg)

    def test_table_query_failed_message(self):
        from app.llm.graphs.analysis_synthesis_v2 import _table_empty_message

        msg = _table_empty_message(
            "overheat_q6_history",
            ("q6c",),
            task_status={"q6c": "mandatory_failed"},
            gathered_data={"q6c": []},
        )
        self.assertIn("查询失败", msg)


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
                    "q1": [{"section": "锅炉台账", "机组名称": "1号锅炉", "锅炉型号": "HG-1000", "额定负荷_MW": 600}],
                    "q2a": [{"测点编号": "T01", "超温总时长_秒": 3600, "受热面名称": "末过"}],
                    "q2b": [{"全事件平均负荷_MW": 520}],
                    "q2c": [{"超温等级": "严重超温", "测点及位置列表": "T01", "测点数量": 1}],
                    "q2d": [{"全事件实测最高壁温": 580}],
                    "q3a": [{"超温区域": "屏过", "最高壁温_℃": 580}],
                    "q3b": [{"测点编号": "T01", "瞬时尖峰超温次数": 2}],
                    "q4a": [{"采集时间": "2026-05-01", "壁温_℃": 575}],
                    "q4b": [{"采集时间": "2026-05-01", "测点数值": 100}],
                    "q5a": [{"record_type": "遗留问题", "问题描述": "减薄"}],
                    "q5b": [{"已恢复严重超温数": 1}],
                    "q6a": [{"section": "壁温趋势", "壁温值": 570, "采集时间": "2026-05-01 10:00:00"}],
                    "q6b": [{"测点编号": "T01", "超温差值_℃": 20}],
                    "q6c": [{"测点编号": "T01", "历史最大超温差值": 18}],
                    "q6d": [
                        {"采集时间": "2026-05-01 10:00", "参数类型": "壁温", "参数值": 575},
                        {"采集时间": "2026-05-01 10:00", "参数类型": "机组负荷", "参数值": 510},
                    ],
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


class TestSynthesisV2OrderedStream(unittest.TestCase):
    def test_live_first_emits_static_before_slow_llm(self):
        mini_slots = [
            SynthesisV2Slot(
                id="s01",
                kind="llm_narrative",
                title="第一章",
                source_item_ids=("q1",),
                narrative_instruction="写第一章",
                stream_live=True,
            ),
            SynthesisV2Slot(
                id="s02",
                kind="static_markdown",
                title="",
                static_body="## 第二章\n\n",
            ),
            SynthesisV2Slot(
                id="s03",
                kind="llm_narrative",
                title="第三章",
                source_item_ids=("q2",),
                narrative_instruction="写第三章",
            ),
        ]
        stream_calls = {"n": 0}

        async def _stream_chat(**_kwargs):
            stream_calls["n"] += 1
            if stream_calls["n"] == 1:
                yield "首章"
                return
            await asyncio.sleep(0.08)
            yield "三章"

        llm = MagicMock()
        llm.stream_chat = _stream_chat
        engine = AnalysisSynthesisV2Engine(
            llm_client=llm,
            prompts=_FakePromptRegistry(),
            gathered_json_max_chars=8000,
            segment_max_tokens=512,
            max_parallel_llm=2,
            table_max_rows=10,
            synthesis_timeout_seconds=30.0,
            stream_chunk_chars=8,
            idle_heartbeat_seconds=0.03,
            emit_structured_sse=False,
        )

        async def _run():
            events: list[dict] = []
            with patch(
                "app.llm.graphs.analysis_synthesis_v2.get_synthesis_v2_slots",
                return_value=mini_slots,
            ):
                async for ev, result in engine.iter_stream_events_live_first(
                    analysis_type="overheat_guidance",
                    query="测试",
                    data_mode="nl2sql",
                    gathered_data={"q1": [{"a": 1}], "q2": [{"b": 2}]},
                    context_snippets=[],
                    planning_context=None,
                    chart_mode="off",
                ):
                    if result is None and ev:
                        events.append(ev)
            return events

        events = asyncio.run(_run())
        kinds = [e.get("event") for e in events]
        deltas = [e.get("text", "") for e in events if e.get("event") == "summary_delta"]
        joined = "".join(deltas)
        self.assertIn("首章", joined)
        self.assertIn("## 第二章", joined)
        self.assertIn("三章", joined)
        idx_ch2 = joined.find("## 第二章")
        idx_ch3 = joined.find("三章")
        self.assertGreater(idx_ch3, idx_ch2)
        self.assertIn("synthesis_loading", kinds)

    def test_live_first_template_s01_uses_deterministic_not_llm(self):
        """s01 为 template_deterministic 时，live_first 不得对其调用 LLM。"""
        overheat_slots = get_synthesis_v2_slots("overheat_guidance")

        llm = MagicMock()
        llm.stream_chat = MagicMock(side_effect=AssertionError("s01 must not stream LLM"))
        llm.chat = AsyncMock(return_value="章节正文")
        engine = AnalysisSynthesisV2Engine(
            llm_client=llm,
            prompts=_FakePromptRegistry(),
            gathered_json_max_chars=8000,
            segment_max_tokens=512,
            max_parallel_llm=2,
            table_max_rows=10,
            synthesis_timeout_seconds=30.0,
            stream_chunk_chars=64,
            idle_heartbeat_seconds=0.05,
            emit_structured_sse=False,
        )

        q1_row = {
            "机组名称": "1号锅炉",
            "锅炉型号": "HG-1000",
            "额定负荷_MW": 600,
            "监测部位": "水冷壁",
            "超温测点总数": 10,
            "轻微超温数量": 1,
            "中度超温数量": 2,
            "严重超温数量": 3,
        }

        async def _run():
            result = None
            deltas: list[str] = []
            async for ev, res in engine.iter_stream_events_live_first(
                analysis_type="overheat_guidance",
                query="请分析1号锅炉今天的超温情况",
                data_mode="nl2sql",
                gathered_data={"q1": [q1_row], "q2a": [], "q2b": [], "q2c": []},
                context_snippets=[],
                planning_context=None,
                chart_mode="off",
            ):
                if res is not None:
                    result = res
                elif ev.get("event") == "summary_delta":
                    deltas.append(ev.get("text", ""))
            return result, "".join(deltas)

        with patch(
            "app.llm.graphs.analysis_synthesis_v2.get_synthesis_v2_slots",
            return_value=overheat_slots[:6],
        ):
            result, joined = asyncio.run(_run())
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("1号锅炉", result.summary)
        self.assertIn("HG-1000", joined)
        self.assertNotIn("### 一、报告基础信息\n\n（待补充）", result.summary)

    def test_ordered_stream_emits_early_slots_before_later_llm(self):
        """iter_stream_events：按注册表顺序推送，第一章先于第三章 LLM。"""
        mini_slots = [
            SynthesisV2Slot(
                id="s01",
                kind="static_markdown",
                title="",
                static_body="### 一、报告基础信息\n\n第一章正文\n\n",
            ),
            SynthesisV2Slot(
                id="s02",
                kind="static_markdown",
                title="",
                static_body="### 二、超温事件概况\n\n",
            ),
            SynthesisV2Slot(
                id="s04a",
                kind="llm_narrative",
                title="",
                source_item_ids=("q3a",),
                narrative_instruction="写第三章统计",
            ),
        ]
        stream_calls = {"n": 0}

        async def _stream_chat(**_kwargs):
            stream_calls["n"] += 1
            await asyncio.sleep(0.05)
            yield "第三章LLM"

        llm = MagicMock()
        llm.stream_chat = _stream_chat
        engine = AnalysisSynthesisV2Engine(
            llm_client=llm,
            prompts=_FakePromptRegistry(),
            gathered_json_max_chars=8000,
            segment_max_tokens=512,
            max_parallel_llm=2,
            table_max_rows=10,
            synthesis_timeout_seconds=30.0,
            stream_chunk_chars=8,
            idle_heartbeat_seconds=0.03,
            emit_structured_sse=False,
        )

        async def _run():
            events: list[dict] = []
            result = None
            with patch(
                "app.llm.graphs.analysis_synthesis_v2.get_synthesis_v2_slots",
                return_value=mini_slots,
            ):
                async for ev, res in engine.iter_stream_events(
                    analysis_type="overheat_guidance",
                    query="测试",
                    data_mode="nl2sql",
                    gathered_data={"q3a": [{"a": 1}]},
                    context_snippets=[],
                    planning_context=None,
                    chart_mode="off",
                ):
                    if res is not None:
                        result = res
                    elif ev:
                        events.append(ev)
            return events, result

        events, result = asyncio.run(_run())
        deltas = [e.get("text", "") for e in events if e.get("event") == "summary_delta"]
        joined = "".join(deltas)
        self.assertIn("一、报告基础信息", joined)
        self.assertIn("二、超温事件概况", joined)
        self.assertIn("第三章LLM", joined)
        idx_ch1 = joined.find("一、报告基础信息")
        idx_ch3 = joined.find("第三章LLM")
        self.assertGreater(idx_ch3, idx_ch1)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("一、报告基础信息", result.summary)

    def test_emit_markdown_chunks_honors_stream_delay(self):
        engine = AnalysisSynthesisV2Engine(
            llm_client=MagicMock(),
            prompts=_FakePromptRegistry(),
            gathered_json_max_chars=8000,
            segment_max_tokens=512,
            max_parallel_llm=1,
            table_max_rows=10,
            synthesis_timeout_seconds=30.0,
            stream_chunk_chars=4,
            stream_chunk_delay_ms=10.0,
            emit_structured_sse=False,
        )

        async def _collect():
            out = []
            async for ev in engine._emit_markdown_chunks("abcdefgh"):
                out.append(ev)
            return out

        t0 = time.perf_counter()
        events = asyncio.run(_collect())
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(2, len(events))
        self.assertGreaterEqual(elapsed_ms, 8.0)

    def test_deterministic_slot_chunked(self):
        engine = AnalysisSynthesisV2Engine(
            llm_client=MagicMock(),
            prompts=_FakePromptRegistry(),
            gathered_json_max_chars=8000,
            segment_max_tokens=512,
            max_parallel_llm=1,
            table_max_rows=10,
            synthesis_timeout_seconds=30.0,
            stream_chunk_chars=4,
            emit_structured_sse=False,
        )
        chunks = engine._chunk_text("abcdefgh", 4)
        self.assertEqual(["abcd", "efgh"], chunks)


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
