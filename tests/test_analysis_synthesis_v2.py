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
    _append_q1_top_points,
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
    build_boiler_time_ranges_from_q0,
    build_overheat_distribution_note,
    build_overheat_region_fact_packages,
    enrich_overheat_report_context_from_gathered,
    expand_overheat_cause_slots,
    format_overheat_entity_label,
    group_q1_rows_by_region,
    infer_overheat_report_context,
    normalize_overheat_region_key,
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
        self.assertEqual(17, len(slots))
        by_id = {s.id: s for s in slots}
        self.assertIn("超温情况概览", OVERHEAT_CH1_INTRO)
        self.assertEqual("q1", by_id["s03_daily_section"].source_item_ids[0])
        self.assertEqual("overheat_daily_section", by_id["s03_daily_section"].template_id)
        self.assertEqual(("q1", "q2", "q3"), by_id["s04_weekly_section"].source_item_ids)
        self.assertTrue(by_id["s06_cause"].stream_live)
        self.assertEqual("", by_id["s06_cause"].title)
        self.assertEqual("紧急处置\n\n", by_id["s10a_emergency_hdr"].static_body)

    def test_daily_mode_skips_weekly_slots(self):
        slots = get_effective_synthesis_v2_slots(
            "overheat_guidance",
            report_context={"analysis_mode": "daily"},
        )
        ids = {s.id for s in slots}
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
            "适当降低机组负荷，并加强重点区域监视。"
        )
        cleaned = _sanitize_report_narrative(raw)
        self.assertNotIn("以下内容为示例", cleaned)
        self.assertIn("适当降低机组负荷", cleaned)

    def test_strips_risk_duplicate_heading(self):
        raw = "超温风险评估\n\n**风险量化：**管材存在蠕变风险。"
        cleaned = _sanitize_report_narrative(raw)
        self.assertNotIn("超温风险评估", cleaned)
        self.assertIn("**风险量化：**", cleaned)

    def test_strips_ch2_ch3_duplicate_section_headings(self):
        cases = [
            ("**超温原因剖析**\n\n概括句。", "概括句。"),
            ("### 超温原因剖析：\n\n概括句。", "概括句。"),
            ("### 2号锅炉超温原因剖析\n\n概括句。", "概括句。"),
            ("**2号锅炉超温原因剖析**：\n\n概括句。", "概括句。"),
            ("**超温风险评估**\n\n风险描述。", "风险描述。"),
            ("### 超温风险评估：\n\n风险描述。", "风险描述。"),
        ]
        for raw, want_start in cases:
            out = _sanitize_report_narrative(raw)
            self.assertTrue(
                out.startswith(want_start),
                msg=f"unexpected sanitize output: {out!r} from {raw!r}",
            )


class TestOverheatRenderers(unittest.TestCase):
    def test_ch1_intro_is_section_header_only(self):
        self.assertEqual("## 超温情况概览\n\n", OVERHEAT_CH1_INTRO)
        self.assertNotIn("Query 中如果是问今天", OVERHEAT_CH1_INTRO)
        self.assertNotIn("该章节数据和报告要求", OVERHEAT_CH1_INTRO)

    def test_authoring_rules_separate_from_report_body(self):
        self.assertIn("禁止出现在报告正文", OVERHEAT_DOCX_AUTHORING_RULES)
        self.assertIn("以下内容为示例", OVERHEAT_DOCX_AUTHORING_RULES)

    def test_daily_section_uses_q0_boiler_time_ranges(self):
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
        ctx = enrich_overheat_report_context_from_gathered(
            {"analysis_mode": "daily"},
            {
                "q0": [{
                    "机组名称": "1号锅炉",
                    "最早超温开始时间": "2026-05-01 08:15:00",
                    "最晚超温结束时间": "2026-05-01 22:40:00",
                }],
            },
        )
        md = render_overheat_daily_section(
            rows,
            report_context=ctx,
            render_table=render_markdown_table,
            max_rows=50,
            empty_message="（无数据）",
        )
        self.assertIn("开始时间：2026-05-01 08:15:00", md)
        self.assertIn("结束时间：2026-05-01 22:40:00", md)
        self.assertNotIn("____年__月__日", md)

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
        self.assertIn("周超温详情：", md)
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
        # 每台锅炉：概览表后、测点详情表前各有「周超温详情：」
        self.assertEqual(2, md.count("周超温详情："))
        self.assertIn("机组信息：2号锅炉", md)
        overview_pos = md.find("周超温概览")
        detail_pos = md.find("周超温详情：")
        p1_pos = md.find("P1")
        self.assertLess(overview_pos, detail_pos)
        self.assertLess(detail_pos, p1_pos)

    def test_distribution_from_q1(self):
        rows = [
            {"受热面名称": "末过", "测点编号": "P1"},
            {"受热面名称": "末过", "测点编号": "P2"},
            {"受热面名称": "水冷壁", "测点编号": "P3"},
        ]
        note = build_overheat_distribution_note(rows, _infer_overheat_distribution)
        self.assertIn("混合型", note)

    def test_region_fact_packages_isolate_soot_by_region(self):
        q1 = [
            {
                "区域名称": "低温再热器 限569℃",
                "受热面名称": "低温再热器",
                "规格材质": "12Cr1MoVG",
                "测点编号": "R1",
                "测点名称": "再热器测点1",
                "最大超温值_℃": 600,
                "最大监测超温差值_℃": 31,
                "异常等级": "Ⅲ级（严重超温）",
            },
            {
                "区域名称": "水冷壁螺旋段后墙 限540℃",
                "受热面名称": "水冷壁螺旋段后墙",
                "规格材质": "20G",
                "测点编号": "W1",
                "测点名称": "螺旋段测点1",
                "最大超温值_℃": 555,
                "最大监测超温差值_℃": 15,
                "异常等级": "Ⅱ级（中度超温）",
            },
        ]
        q6 = [
            {"受热面名称": "低温再热器", "吹灰次数": 8, "吹灰天数": 3},
            {"受热面名称": "水冷壁螺旋段后墙", "吹灰次数": 2, "吹灰天数": 1},
            {"受热面名称": "水冷壁螺旋段前墙", "吹灰次数": 2, "吹灰天数": 1},
        ]
        grouped = group_q1_rows_by_region(q1)
        self.assertEqual({"低温再热器", "水冷壁螺旋段后墙"}, set(grouped.keys()))
        self.assertEqual("低温再热器", normalize_overheat_region_key(q1[0]))
        pkg = build_overheat_region_fact_packages(q1, q6)
        self.assertIn("区域1：低温再热器", pkg)
        self.assertIn("本区测点（仅此区域", pkg)
        self.assertIn("再热器测点1（R1）", pkg)
        self.assertNotIn("R1(再热器测点1)", pkg)
        self.assertIn("本区吹灰: 吹灰8次", pkg)
        self.assertIn("本区规格材质: 12Cr1MoVG", pkg)
        self.assertIn("区域2：水冷壁螺旋段后墙", pkg)
        self.assertIn("螺旋段测点1（W1）", pkg)
        self.assertIn("本区吹灰: 吹灰2次", pkg)
        self.assertIn("本区规格材质: 20G", pkg)
        self.assertIn("跨区域禁令", pkg)
        reheater_block = pkg.split("区域2：")[0]
        self.assertNotIn("水冷壁螺旋段", reheater_block.split("区域1：", 1)[1])
        self.assertNotIn("螺旋段测点1", reheater_block)


class TestOverheatEntityLabel(unittest.TestCase):
    def test_format_name_with_code(self):
        self.assertEqual("测点A（P001）", format_overheat_entity_label("测点A", "P001"))

    def test_format_name_only(self):
        self.assertEqual("测点A", format_overheat_entity_label("测点A", None))

    def test_format_code_only(self):
        self.assertEqual("P001", format_overheat_entity_label(None, "P001"))

    def test_q1_top_points_use_display_label(self):
        lines = ["【可引用事实】"]
        rows = [
            {
                "测点编号": "P1",
                "测点名称": "壁温测点1",
                "最大监测超温差值_℃": 25,
            }
        ]
        _append_q1_top_points(lines, rows)
        joined = "\n".join(lines)
        self.assertIn("壁温测点1（P1）", joined)
        self.assertNotIn("P1(壁温测点1)", joined)


class TestSynthesisV2NarrativeHelpers(unittest.TestCase):
    def test_q5_audit_preview_limit_for_cause_slot(self):
        self.assertEqual(24, _audit_preview_row_limit("q5", 100, slot_id="s06_cause"))
        self.assertEqual(24, _audit_preview_row_limit("q5", 100, slot_id="s10a_emergency"))

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
        self.assertNotIn("吹灰频次偏低", text)

    def test_audit_facts_q6_global_hint_for_non_cause_slot(self):
        text = _build_audit_facts(
            {"q6": [{"机组名称": "1号锅炉", "受热面名称": "水冷壁", "吹灰次数": 1}]},
            "1号锅炉",
            slot_id="s10a_emergency",
        )
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

    def test_sanitize_strips_ch4_duplicate_section_headings(self):
        cases = [
            ("**后续检修预防措施**\n\n1. 氧化皮检测", "1. 氧化皮检测"),
            ("### 后续检修预防措施：\n\n1. 氧化皮检测", "1. 氧化皮检测"),
            ("### 运行优化调整\n\n建议A", "建议A"),
            ("**计划长效防控方案**：\n\n方案A", "方案A"),
            ("紧急处置措施\n\n处置A", "处置A"),
            ("### **紧急处置措施**\n\n处置B", "处置B"),
        ]
        for raw, want_start in cases:
            out = _sanitize_report_narrative(raw)
            self.assertTrue(
                out.startswith(want_start),
                msg=f"unexpected sanitize output: {out!r} from {raw!r}",
            )

    def test_audit_facts_q1(self):
        text = _build_audit_facts(
            {"q1": [{"测点编号": "P1", "最大超温值_℃": 569}]},
            "1号锅炉",
            slot_id="s06_cause",
        )
        self.assertIn("测点超温明细", text)
        self.assertIn("P1", text)

    def test_live_slot_is_cause(self):
        gathered = {
            "q1": [{"机组名称": "1号锅炉", "测点编号": "P1", "最大超温值_℃": 569}],
        }
        slots = get_effective_synthesis_v2_slots(
            "overheat_guidance",
            report_context={"analysis_mode": "daily"},
            gathered_data=gathered,
        )
        idx = _resolve_live_slot_index(slots)
        self.assertEqual("s06_cause__0", slots[idx].id)
        self.assertEqual("1号锅炉", slots[idx].boiler_name)

    def test_expand_cause_slots_per_boiler(self):
        base = get_synthesis_v2_slots("overheat_guidance")
        gathered = {
            "q1": [
                {"机组名称": "1号锅炉"},
                {"机组名称": "2号锅炉"},
            ],
        }
        expanded = expand_overheat_cause_slots(base, gathered)
        ids = [s.id for s in expanded]
        self.assertEqual(
            ["s06_cause_hdr__0", "s06_cause__0", "s06_cause_hdr__1", "s06_cause__1"],
            [i for i in ids if i.startswith("s06_cause")],
        )
        hdr0 = next(s for s in expanded if s.id == "s06_cause_hdr__0")
        self.assertEqual("### 1号锅炉超温原因剖析\n\n", hdr0.static_body)
        self.assertEqual("1号锅炉", next(s for s in expanded if s.id == "s06_cause__0").boiler_name)
        self.assertEqual("2号锅炉", next(s for s in expanded if s.id == "s06_cause__1").boiler_name)

    def test_build_boiler_time_ranges_from_q0(self):
        ranges = build_boiler_time_ranges_from_q0([
            {"机组名称": "1号锅炉", "最早超温开始时间": "2026-05-01 08:00", "最晚超温结束时间": "2026-05-01 20:00"},
            {"机组名称": "2号锅炉", "最早超温开始时间": "2026-05-01 09:00", "最晚超温结束时间": "2026-05-01 21:00"},
        ])
        self.assertEqual("2026-05-01 08:00", ranges["1号锅炉"]["t_start"])
        self.assertEqual("2026-05-01 20:00", ranges["1号锅炉"]["t_end"])


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
            "q0": [{
                "机组名称": "1号锅炉",
                "最早超温开始时间": "2026-05-01 08:15:00",
                "最晚超温结束时间": "2026-05-01 22:40:00",
            }],
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
            report_context=enrich_overheat_report_context_from_gathered(
                {"analysis_mode": "daily"}, gathered
            ),
        )
        self.assertIn("锅炉管壁超温智能分析报告", result.summary)
        self.assertIn("超温情况概览", result.summary)
        self.assertNotIn("--按日超温分析--", result.summary)
        self.assertIn("**机组信息：1号锅炉", result.summary)
        self.assertIn("开始时间：2026-05-01 08:15:00", result.summary)
        self.assertNotIn("____年__月__日", result.summary)
        self.assertIn("## 超温原因剖析", result.summary)
        self.assertIn("### 1号锅炉超温原因剖析", result.summary)
        self.assertIn("紧急处置", result.summary)
        self.assertNotIn("该章节数据和报告要求", result.summary)
        self.assertNotIn("以下内容为示例", result.summary)
        self.assertNotIn("Query 中如果是问", result.summary)
        self.assertNotIn("--按周超温分析--", result.summary)
        self.assertNotIn("### 超温原因剖析", result.summary)
        self.assertGreaterEqual(llm.chat.await_count, 1)


if __name__ == "__main__":
    unittest.main()
