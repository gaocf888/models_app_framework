"""Tests for 20260602 overheat synthesis v2 (four-section template)."""

import asyncio
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.analysis_service import _encode_sse_event

from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.llm.graphs.analysis_synthesis_v2 import (
    AnalysisSynthesisV2Engine,
    SynthesisV2Slot,
    _append_q5_sis_agg_hint,
    _append_q6_soot_agg_hint,
    _append_q7_mill_agg_hint,
    _audit_preview_row_limit,
    _build_audit_facts,
    _infer_overheat_distribution,
    _join_slot_markdown,
    _rag_snippets_for_slot,
    _resolve_data_subset,
    _resolve_live_slot_index,
    _sanitize_report_narrative,
    get_effective_synthesis_v2_slots,
    get_synthesis_v2_slots,
    render_markdown_table,
    strip_leading_duplicate_heading,
    synthesis_v2_registry_available,
)
from app.llm.graphs.overheat_synthesis_render import (
    OVERHEAT_CH1_INTRO,
    OVERHEAT_DOCX_AUTHORING_RULES,
    build_overheat_distribution_note,
    filter_overheat_slot_ids,
    infer_overheat_report_context,
    render_overheat_daily_section,
    render_overheat_weekly_section,
)


class TestSseEventJsonEncoding(unittest.TestCase):
    def test_table_payload_decimal_serializable(self):
        payload = {
            "event": "table_payload",
            "slot_id": "s03_daily_section",
            "table": {"rows": [{"最大超温值": Decimal("580.5")}]},
        }
        raw = _encode_sse_event(payload).decode("utf-8")
        data = json.loads(raw[6:].strip())
        self.assertEqual("580.5", data["table"]["rows"][0]["最大超温值"])


class TestSynthesisV2Registry(unittest.TestCase):
    def test_overheat_registry_slot_count(self):
        self.assertTrue(synthesis_v2_registry_available("overheat_guidance"))
        slots = get_synthesis_v2_slots("overheat_guidance")
        self.assertEqual(12, len(slots))
        by_id = {s.id: s for s in slots}
        self.assertIn("超温情况概览", OVERHEAT_CH1_INTRO)
        self.assertEqual("q1", by_id["s03_daily_section"].source_item_ids[0])
        self.assertEqual("overheat_daily_section", by_id["s03_daily_section"].template_id)
        self.assertEqual(("q1", "q2", "q3"), by_id["s04_weekly_section"].source_item_ids)
        self.assertTrue(by_id["s06_cause"].stream_live)
        self.assertEqual("", by_id["s06_cause"].title)

    def test_daily_mode_skips_weekly_slots(self):
        slots = get_effective_synthesis_v2_slots(
            "overheat_guidance",
            report_context={"analysis_mode": "daily"},
        )
        ids = {s.id for s in slots}
        self.assertIn("s02_daily_marker", ids)
        self.assertIn("s03_daily_section", ids)
        self.assertNotIn("s02_weekly_marker", ids)
        self.assertNotIn("s04_weekly_section", ids)

    def test_weekly_mode_skips_daily_table(self):
        slots = get_effective_synthesis_v2_slots(
            "overheat_guidance",
            report_context={"analysis_mode": "weekly"},
        )
        ids = {s.id for s in slots}
        self.assertNotIn("s02_daily_marker", ids)
        self.assertNotIn("s03_daily_section", ids)
        self.assertIn("s02_weekly_marker", ids)
        self.assertIn("s04_weekly_section", ids)

    def test_unknown_type_no_registry(self):
        self.assertFalse(synthesis_v2_registry_available("unknown_type"))


class TestOverheatReportContext(unittest.TestCase):
    def test_infer_daily_from_query(self):
        ctx = infer_overheat_report_context("请分析昨天1号锅炉超温情况")
        self.assertEqual("daily", ctx["analysis_mode"])

    def test_infer_weekly_default(self):
        ctx = infer_overheat_report_context("请分析1号锅炉本周超温")
        self.assertEqual("weekly", ctx["analysis_mode"])


class TestSanitizeNarrative(unittest.TestCase):
    def test_strips_docx_instruction_lines(self):
        raw = (
            "关联本次出现的超温区域，根据知识片段出具针对性措施，以下内容为示例：\n\n"
            "紧急处置\n适当降低机组负荷。"
        )
        cleaned = _sanitize_report_narrative(raw)
        self.assertNotIn("以下内容为示例", cleaned)
        self.assertIn("紧急处置", cleaned)


class TestOverheatRenderers(unittest.TestCase):
    def test_ch1_intro_is_section_header_only(self):
        self.assertEqual("## 超温情况概览\n\n", OVERHEAT_CH1_INTRO)
        self.assertNotIn("Query 中如果是问今天", OVERHEAT_CH1_INTRO)
        self.assertNotIn("该章节数据和报告要求", OVERHEAT_CH1_INTRO)

    def test_authoring_rules_separate_from_report_body(self):
        self.assertIn("禁止出现在报告正文", OVERHEAT_DOCX_AUTHORING_RULES)
        self.assertIn("以下内容为示例", OVERHEAT_DOCX_AUTHORING_RULES)

    def test_daily_section_structure(self):
        rows = [
            {
                "机组名称": "1号锅炉",
                "区域名称": "水冷壁 限540℃",
                "测点编号": "P1",
                "测点名称": "测点1",
                "最大超温值_℃": 569,
                "最小超温值_℃": 499,
                "最大连续超温时长_分钟": 301,
                "超温日期": "2026.05.02 10:00:01",
                "异常等级": "Ⅰ级（轻微超温）",
            }
        ]
        md = render_overheat_daily_section(
            rows,
            report_context={"analysis_mode": "daily", "t_start": "2026-05-01", "t_end": "2026-05-02"},
            render_table=render_markdown_table,
            max_rows=50,
            empty_message="（无数据）",
        )
        self.assertIn("机组信息：1号锅炉", md)
        self.assertIn("开始时间：2026-05-01", md)
        self.assertNotIn("####", md)
        self.assertIn("569℃", md)
        self.assertIn("301分", md)
        self.assertIn("最大超温值", md)
        self.assertNotIn("最大超温值_℃", md)

    def test_weekly_section_structure(self):
        q1 = [{
            "机组名称": "1号锅炉",
            "区域名称": "水冷壁 限540℃",
            "测点名称": "测点1",
            "最大超温值_℃": 569,
            "最小超温值_℃": 499,
            "最大连续超温时长_分钟": 15,
            "超温日期": "2026.05.12 10:00:00",
            "异常等级": "Ⅰ级（轻微超温）",
        }]
        q2 = [{
            "机组名称": "1号锅炉",
            "区域名称": "水冷壁 限540℃",
            "受热面名称": "水冷壁",
            "超温点数": 2,
            "周最大超温值_℃": 569,
            "周最小超温值_℃": 499,
            "周最大连续超温时长_分钟": 301,
            "Ⅰ级数量": 1,
            "Ⅱ级数量": 1,
            "Ⅲ级数量": 0,
            "Ⅳ级数量": 0,
        }]
        q3 = [{
            "机组名称": "1号锅炉",
            "受热面名称": "水冷壁",
            "超温日期": "2026-05-12",
            "当日超温测点数": 1,
        }]
        md = render_overheat_weekly_section(
            q1, q2, q3,
            report_context={"analysis_mode": "weekly", "t_start": "2026-05-10", "t_end": "2026-05-16"},
            render_table=render_markdown_table,
            max_rows=50,
            empty_message="（无数据）",
        )
        self.assertIn("周超温概览：开始时间", md)
        self.assertIn("12日", md)
        self.assertIn("Ⅰ级（轻微超温）1个", md)
        self.assertIn("测点1", md)
        self.assertNotIn("####", md)

    def test_weekly_multi_boiler_detail_header(self):
        q1 = [
            {"机组名称": "1号锅炉", "区域名称": "A", "测点名称": "P1", "最大超温值_℃": 1,
             "最小超温值_℃": 1, "最大连续超温时长_分钟": 1, "超温日期": "x", "异常等级": "Ⅰ级"},
            {"机组名称": "2号锅炉", "区域名称": "B", "测点名称": "P2", "最大超温值_℃": 2,
             "最小超温值_℃": 2, "最大连续超温时长_分钟": 2, "超温日期": "y", "异常等级": "Ⅱ级"},
        ]
        md = render_overheat_weekly_section(
            q1, [], [],
            report_context={"analysis_mode": "weekly"},
            render_table=render_markdown_table,
            max_rows=50,
            empty_message="（无数据）",
        )
        self.assertIn("机组信息：1号锅炉", md)
        self.assertIn("周超温详情：", md)
        self.assertIn("机组信息：2号锅炉", md)

    def test_distribution_from_q1(self):
        rows = [
            {"受热面名称": "末过", "测点编号": "P1"},
            {"受热面名称": "末过", "测点编号": "P2"},
            {"受热面名称": "水冷壁", "测点编号": "P3"},
        ]
        note = build_overheat_distribution_note(rows, _infer_overheat_distribution)
        self.assertIn("混合型", note)


class TestSynthesisV2NarrativeHelpers(unittest.TestCase):
    def test_q5_audit_preview_limit_for_cause_slot(self):
        self.assertEqual(24, _audit_preview_row_limit("q5", 100, slot_id="s06_cause"))
        self.assertEqual(24, _audit_preview_row_limit("q5", 100, slot_id="s10_measures"))

    def test_q5_sis_agg_audit_hint(self):
        lines: list[str] = []
        _append_q5_sis_agg_hint(lines, [
            {"参数类型": "减温水", "测点编码": "10FWFLOW", "采样点数": 120, "最小值": 10, "最大值": 50},
            {"参数类型": "减温水", "测点编码": "10FWTEMP", "采样点数": 120, "最小值": 200, "最大值": 400},
        ])
        joined = "\n".join(lines)
        self.assertIn("SIS关联参数汇总", joined)
        self.assertIn("减温水", joined)
        self.assertIn("未接入参数类型", joined)
        self.assertIn("烟温", joined)

    def test_audit_facts_q5_empty_pending(self):
        text = _build_audit_facts(
            {"q5": []},
            "1号锅炉",
            slot_id="s06_cause",
            task_status={"q5": "success"},
        )
        self.assertIn("SIS关联参数汇总", text)
        self.assertIn("待补充", text)

    def test_audit_facts_q5_aggregated_label(self):
        text = _build_audit_facts(
            {"q5": [{"参数类型": "减温水", "测点编码": "10FWFLOW", "采样点数": 10, "最小值": 1, "最大值": 5}]},
            "1号锅炉",
            slot_id="s06_cause",
        )
        self.assertIn("SIS关联参数汇总", text)
        self.assertIn("减温水", text)
        self.assertIn("未接入参数类型", text)

    def test_q6_audit_preview_limit_for_cause_slot(self):
        self.assertEqual(24, _audit_preview_row_limit("q6", 100, slot_id="s06_cause"))
        self.assertEqual(3, _audit_preview_row_limit("q6", 100, slot_id="s05_other"))

    def test_q6_soot_agg_audit_hint(self):
        lines: list[str] = []
        _append_q6_soot_agg_hint(lines, [
            {"机组名称": "1号锅炉", "受热面名称": "水冷壁", "吹灰次数": 2, "吹灰天数": 1},
            {"机组名称": "1号锅炉", "受热面名称": "过热器", "吹灰次数": 15, "吹灰天数": 5},
        ])
        joined = "\n".join(lines)
        self.assertIn("吹灰频次偏低", joined)
        self.assertIn("水冷壁", joined)
        self.assertIn("2次", joined)

    def test_audit_facts_q6_aggregated_label(self):
        text = _build_audit_facts(
            {"q6": [{"机组名称": "1号锅炉", "受热面名称": "水冷壁", "吹灰次数": 3}]},
            "1号锅炉",
            slot_id="s06_cause",
        )
        self.assertIn("吹灰区域汇总", text)
        self.assertIn("吹灰频次偏低", text)

    def test_q7_audit_preview_limit_for_cause_slot(self):
        self.assertEqual(24, _audit_preview_row_limit("q7", 100, slot_id="s06_cause"))

    def test_q7_mill_agg_audit_hint(self):
        lines: list[str] = []
        _append_q7_mill_agg_hint(lines, [
            {"机组名称": "1号锅炉", "磨煤机名称": "A磨", "采样记录数": 5,
             "平均给煤量_t_h": 40, "最大给煤量_t_h": 60},
            {"机组名称": "1号锅炉", "磨煤机名称": "B磨", "采样记录数": 120,
             "平均给煤量_t_h": 35, "最大给煤量_t_h": 38},
        ])
        joined = "\n".join(lines)
        self.assertIn("磨煤机采样偏少", joined)
        self.assertIn("A磨", joined)
        self.assertIn("给煤波动", joined)

    def test_audit_facts_q7_aggregated_label(self):
        text = _build_audit_facts(
            {"q7": [{"机组名称": "1号锅炉", "磨煤机名称": "A磨", "采样记录数": 10}]},
            "1号锅炉",
            slot_id="s06_cause",
        )
        self.assertIn("磨煤机运行汇总", text)

    def test_sanitize_report_narrative(self):
        raw = "原因A [置信度：高]（依据：q1 测点明细）。"
        out = _sanitize_report_narrative(raw)
        self.assertNotIn("置信度", out)
        self.assertNotIn("q1", out.lower())

    def test_audit_facts_q1(self):
        text = _build_audit_facts(
            {"q1": [{"测点编号": "P1", "最大超温值_℃": 569}]},
            "1号锅炉",
            slot_id="s06_cause",
        )
        self.assertIn("测点超温明细", text)
        self.assertIn("P1", text)

    def test_live_slot_is_cause(self):
        slots = get_synthesis_v2_slots("overheat_guidance")
        idx = _resolve_live_slot_index(slots)
        self.assertEqual("s06_cause", slots[idx].id)


class TestJoinSlotMarkdown(unittest.TestCase):
    def test_join_inserts_blank_line_between_slots(self):
        md = _join_slot_markdown(["# 标题\n", "## 章节\n", "| a | b |\n| --- | --- |\n"])
        self.assertIn("# 标题\n\n## 章节", md)
        self.assertIn("## 章节\n\n| a | b |", md)


class TestAnalysisSynthesisV2Engine(unittest.IsolatedAsyncioTestCase):
    async def test_run_sync_daily_minimal(self):
        prompts = MagicMock()
        prompts.get_template.return_value = SimpleNamespace(content="system")
        llm = AsyncMock()
        llm.chat.return_value = "原因分析正文"
        engine = AnalysisSynthesisV2Engine(
            llm_client=llm,
            prompts=prompts,
            gathered_json_max_chars=8000,
            segment_max_tokens=1024,
            max_parallel_llm=2,
            table_max_rows=20,
            synthesis_timeout_seconds=60.0,
            emit_structured_sse=False,
        )
        gathered = {
            "q1": [{
                "机组名称": "1号锅炉",
                "区域名称": "水冷壁 限540℃",
                "测点名称": "测点1",
                "测点编号": "P1",
                "最大超温值_℃": 569,
                "最小超温值_℃": 499,
                "最大连续超温时长_分钟": 10,
                "超温日期": "2026.05.02",
                "异常等级": "Ⅰ级（轻微超温）",
                "受热面名称": "水冷壁",
            }],
            "q4": [{"机组名称": "1号锅炉", "平均负荷_MW": 480}],
        }
        result = await engine.run_sync(
            analysis_type="overheat_guidance",
            query="昨天1号锅炉超温",
            data_mode="nl2sql",
            gathered_data=gathered,
            context_snippets=[],
            planning_context=None,
            chart_mode="off",
            report_context={"analysis_mode": "daily"},
        )
        self.assertIn("锅炉管壁超温智能分析报告", result.summary)
        self.assertIn("超温情况概览", result.summary)
        self.assertIn("--按日超温分析--", result.summary)
        self.assertIn("机组信息：1号锅炉", result.summary)
        self.assertIn("## 超温原因剖析", result.summary)
        self.assertNotIn("该章节数据和报告要求", result.summary)
        self.assertNotIn("以下内容为示例", result.summary)
        self.assertNotIn("Query 中如果是问", result.summary)
        self.assertNotIn("--按周超温分析--", result.summary)
        self.assertNotIn("### 超温原因剖析", result.summary)
        self.assertGreaterEqual(llm.chat.await_count, 1)


if __name__ == "__main__":
    unittest.main()
