import unittest

from app.llm.graphs.analysis_img_diag_runner import (
    IMG_DIAG_DEFECT_IDENT_TYPE,
    IMG_DIAG_LEAKAGE_BURST_TYPE,
    AnalysisImgDiagGraphRunner,
    _IMG_DIAG_PROFILES,
)
from app.models.analysis import AnalysisImgDiagRequest, AnalysisOptions


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


if __name__ == "__main__":
    unittest.main()
