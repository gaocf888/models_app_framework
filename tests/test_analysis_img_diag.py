import unittest
from unittest.mock import MagicMock

from app.llm.graphs.analysis_img_diag_runner import (
    IMG_DIAG_DEFECT_IDENT_TYPE,
    IMG_DIAG_LEAKAGE_BURST_TYPE,
    AnalysisImgDiagGraphRunner,
    _IMG_DIAG_PROFILES,
)
from app.models.analysis import AnalysisImgDiagRequest, AnalysisNL2SQLCall, AnalysisOptions


class TestAnalysisImgDiagSubtypes(unittest.TestCase):
    def test_profiles_analysis_types(self) -> None:
        self.assertEqual(_IMG_DIAG_PROFILES["defect_ident"].analysis_type, IMG_DIAG_DEFECT_IDENT_TYPE)
        self.assertEqual(_IMG_DIAG_PROFILES["leakage_burst"].analysis_type, IMG_DIAG_LEAKAGE_BURST_TYPE)

    def test_gathered_data_for_synthesis_uses_purpose_labels(self) -> None:
        labeled = AnalysisImgDiagGraphRunner._gathered_data_for_synthesis(
            {"q1": [{"a": 1}], "q2a": [{"b": 2}]},
            [
                {"item_id": "q1", "purpose": "管段基础参数"},
                {"item_id": "q2a", "purpose": "检修处置-近3次壁厚"},
            ],
            analysis_type=IMG_DIAG_DEFECT_IDENT_TYPE,
        )
        self.assertIn("管段基础参数", labeled)
        self.assertIn("检修处置-近3次壁厚", labeled)
        self.assertNotIn("q1", labeled)
        self.assertNotIn("q2a", labeled)

    def test_business_rag_query_defect_ident(self) -> None:
        req = AnalysisImgDiagRequest(
            user_id="u_img",
            session_id="s_img",
            img_diag_subtype="defect_ident",
            query="请识别缺陷并给出处置建议，1号锅炉低温过热器A侧第2排第3根",
            image_urls=["http://x/y.png"],
            options=AnalysisOptions(enable_rag=True),
        )
        vision = {
            "defect_type": "飞灰冲刷磨损沟槽",
            "defect_signals": ["纵向沟槽"],
            "risk_level": "moderate",
            "affected_surface": "过热器",
        }
        rq = AnalysisImgDiagGraphRunner.business_rag_query(
            req,
            vision,
            parsed_scope={"boiler": "1号锅炉", "device_name": "低温过热器", "row_no": 2, "tube_no": 3},
        )
        self.assertIn("1号锅炉低温过热器", rq)
        self.assertIn("飞灰冲刷磨损沟槽", rq)
        self.assertIn("缺陷识别", rq)
        self.assertIn("row_no=2", rq)
        self.assertIn("历史处置", rq)

    def test_business_rag_query_leakage_burst(self) -> None:
        req = AnalysisImgDiagRequest(
            user_id="u_lb",
            session_id="s_lb",
            img_diag_subtype="leakage_burst",
            query="#2炉高温过热器B侧第4排于2025-03-01 14:00发生泄爆，请分析原因",
            image_urls=[],
            options=AnalysisOptions(enable_rag=True),
        )
        vision = {
            "burst_type": "环向开口爆口",
            "burst_signals": ["边缘明显减薄"],
            "severity": "high",
            "affected_surface": "过热器",
        }
        rq = AnalysisImgDiagGraphRunner.business_rag_query(req, vision)
        self.assertIn("泄爆分析", rq)
        self.assertIn("环向开口爆口", rq)
        self.assertIn("历史事故案例", rq)

    def test_leakage_burst_allows_no_images(self) -> None:
        req = AnalysisImgDiagRequest(
            user_id="u1",
            session_id="s1",
            img_diag_subtype="leakage_burst",
            query="1号炉水冷壁前墙第1排于2025-01-10 08:00泄爆",
            image_urls=[],
        )
        self.assertEqual(req.img_diag_subtype, "leakage_burst")
        self.assertEqual(len(req.image_urls), 0)

    def test_defect_ident_requires_images(self) -> None:
        with self.assertRaises(ValueError):
            AnalysisImgDiagRequest(
                user_id="u1",
                session_id="s1",
                img_diag_subtype="defect_ident",
                query="位置：2号炉水冷壁前墙第1排",
                image_urls=[],
            )

    def test_img_diag_summary_user_content_preserves_vision_findings(self) -> None:
        runner = AnalysisImgDiagGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=MagicMock(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_gathered_json_max_chars = 2000
        vision = {
            "burst_type": "环向开口爆口",
            "location_visual": "12号管排可见开口",
            "burst_signals": ["边缘减薄"],
        }
        huge_rows = [{"row": i, "payload": "x" * 200} for i in range(80)]
        content = runner._build_img_diag_summary_user_content(
            query="泄爆分析",
            analysis_type=IMG_DIAG_LEAKAGE_BURST_TYPE,
            data_mode="img_diag_leakage_burst",
            data_blob={
                "vision_findings": vision,
                "structured_queries_snapshot": {"q1": huge_rows},
            },
            context_snippets=[],
        )
        self.assertIn("环向开口爆口", content)
        self.assertIn("12号管排可见开口", content)
        self.assertIn("视觉结构化结果", content)
        self.assertIn("禁止写未提供/无图", content)

    def test_prepare_synthesis_queries_caps_historical_and_marks_empty(self) -> None:
        rows_q2b = [{"管排号": i, "年平均减薄速率": "0.1"} for i in range(50)]
        calls = [
            AnalysisNL2SQLCall(
                item_id="q2b",
                purpose="检修处置-减薄速率",
                question="q",
                row_count=911,
                status="success",
            ),
            AnalysisNL2SQLCall(
                item_id="q3",
                purpose="壁温超温数据",
                question="q",
                row_count=0,
                status="success",
            ),
        ]
        plan_tasks = [
            {"item_id": "q2b", "purpose": "检修处置-减薄速率"},
            {"item_id": "q3", "purpose": "壁温超温数据"},
        ]
        snapshot, catalog = AnalysisImgDiagGraphRunner._prepare_img_diag_synthesis_queries(
            {"q2b": rows_q2b, "q3": []},
            plan_tasks,
            calls,
            analysis_type=IMG_DIAG_LEAKAGE_BURST_TYPE,
        )
        self.assertEqual(12, len(snapshot["检修处置-减薄速率"]))
        q2b_cat = next(c for c in catalog if c["purpose"] == "检修处置-减薄速率")
        self.assertEqual(911, q2b_cat["row_count"])
        self.assertEqual("historical_no_time_filter", q2b_cat["time_scope"])
        self.assertIn("historical", q2b_cat["synthesis_status"])
        q3_cat = next(c for c in catalog if c["purpose"] == "壁温超温数据")
        self.assertEqual(0, q3_cat["row_count"])
        self.assertEqual("empty", q3_cat["synthesis_status"])
        self.assertIn("库表未检索到", q3_cat["synthesis_rule"])

    def test_img_diag_summary_user_content_includes_queries_catalog(self) -> None:
        runner = AnalysisImgDiagGraphRunner(
            conv_manager=MagicMock(),
            llm_client=MagicMock(),
            prompt_registry=MagicMock(),
            hybrid_rag=MagicMock(),
            nl2sql_service=MagicMock(),
        )
        runner._analysis_cfg.synthesis_gathered_json_max_chars = 8000
        catalog = [
            {
                "purpose": "壁温超温数据",
                "row_count": 0,
                "synthesis_status": "empty",
                "synthesis_rule": "库表未检索到任何记录",
            }
        ]
        content = runner._build_img_diag_summary_user_content(
            query="泄爆分析",
            analysis_type=IMG_DIAG_LEAKAGE_BURST_TYPE,
            data_mode="img_diag_leakage_burst",
            data_blob={
                "vision_findings": {"burst_type": "穿孔"},
                "structured_queries_catalog": catalog,
                "structured_queries_snapshot": {},
            },
            context_snippets=[],
        )
        self.assertIn("库表查询目录", content)
        self.assertIn("壁温超温数据", content)
        self.assertIn("synthesis_status", content)


if __name__ == "__main__":
    unittest.main()
