import unittest

from app.llm.graphs.analysis_img_diag_runner import AnalysisImgDiagGraphRunner
from app.models.analysis import AnalysisImgDiagRequest, AnalysisOptions


class TestAnalysisImgDiagHelpers(unittest.TestCase):
    def test_bridge_nl2sql_query_contains_unit_and_location(self) -> None:
        req = AnalysisImgDiagRequest(
            user_id="u_img",
            session_id="s_img",
            unit_id="UNIT-02",
            leak_location_text="#2炉高温过热器B侧第4排",
            query="分析爆管原因",
            image_urls=["http://example.com/a.jpg"],
            leak_location_struct={"row": "4"},
            options=AnalysisOptions(),
        )
        q = AnalysisImgDiagGraphRunner.bridge_nl2sql_query(req)
        self.assertIn("UNIT-02", q)
        self.assertIn("#2炉高温过热器B侧第4排", q)
        self.assertIn("分析爆管原因", q)
        self.assertIn("看图诊断", q)

    def test_business_rag_query_contains_unit(self) -> None:
        req = AnalysisImgDiagRequest(
            user_id="u_img",
            session_id="s_img",
            unit_id="U1",
            leak_location_text="位置A",
            query="什么原因",
            image_urls=["http://x/y.png"],
            options=AnalysisOptions(enable_rag=True),
        )
        rq = AnalysisImgDiagGraphRunner.business_rag_query(req)
        self.assertIn("U1", rq)
        self.assertIn("位置A", rq)


if __name__ == "__main__":
    unittest.main()
