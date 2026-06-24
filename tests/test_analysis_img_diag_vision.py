import unittest

from app.llm.graphs.analysis_img_diag_vision import (
    build_vision_rag_hint_query,
    format_vision_rag_hints_block,
)
from app.llm.graphs.analysis_img_diag_runner import _IMG_DIAG_PROFILES
from app.models.analysis import AnalysisImgDiagRequest, AnalysisOptions


class TestAnalysisImgDiagVisionHelpers(unittest.TestCase):
    def test_build_vision_rag_hint_query_defect(self) -> None:
        req = AnalysisImgDiagRequest(
            user_id="u",
            session_id="s",
            img_diag_subtype="defect_ident",
            query="1号炉低温过热器缺陷识别",
            image_urls=["http://x/a.jpg"],
        )
        prof = _IMG_DIAG_PROFILES["defect_ident"]
        q = build_vision_rag_hint_query(
            req,
            rag_scene_label=prof.rag_scene_label,
            hint_intent=prof.vision_rag_hint_intent,
        )
        self.assertIn("缺陷识别", q)
        self.assertIn("TOP10", q)
        self.assertIn("可见形貌", q)

    def test_format_vision_rag_hints_uses_snippets(self) -> None:
        snippets = [
            "飞灰冲刷：平行沟槽形貌，沿烟气流向",
            "点蚀：表面麻点状腐蚀坑",
        ]
        block, items = format_vision_rag_hints_block(snippets, top_n=10, subtype="defect_ident")
        self.assertIn("知识库召回", block)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0].startswith("1."))

    def test_format_vision_rag_hints_fallback_when_empty(self) -> None:
        block, items = format_vision_rag_hints_block([], top_n=10, subtype="defect_ident")
        self.assertIn("内置对照清单", block)
        self.assertEqual(len(items), 10)
        self.assertIn("飞灰冲刷", items[0])

    def test_format_vision_rag_hints_burst_fallback(self) -> None:
        block, items = format_vision_rag_hints_block([], top_n=5, subtype="leakage_burst")
        self.assertIn("爆口", block)
        self.assertEqual(len(items), 5)

    def test_profile_has_observe_scene_and_rag_hint_intent(self) -> None:
        for key in ("defect_ident", "leakage_burst"):
            prof = _IMG_DIAG_PROFILES[key]
            self.assertTrue(prof.vision_observe_scene)
            self.assertTrue(prof.vision_rag_hint_intent)
            self.assertIn("observe", prof.vision_observe_scene)


if __name__ == "__main__":
    unittest.main()
