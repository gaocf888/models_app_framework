"""综合分析 rag_citations 构建（与智能客服字段对齐）。"""

from __future__ import annotations

from app.llm.graphs.analysis_graph_runner import AnalysisGraphRunner
from app.rag.models import RetrievedChunk


def test_build_analysis_rag_citations_includes_original_content_url() -> None:
    chunk = RetrievedChunk(
        text="超温记录表示例",
        doc_name="overheat_guide.docx",
        namespace="global",
        chunk_id="c1",
        score=0.9,
        metadata={"content_fetched_from_url": "https://cdn.example.com/overheat_guide.docx"},
    )
    cites = AnalysisGraphRunner._build_analysis_rag_citations(business_chunks=[chunk])
    assert len(cites) == 1
    assert cites[0]["doc_name"] == "overheat_guide.docx"
    assert cites[0]["original_content_url"] == "https://cdn.example.com/overheat_guide.docx"


def test_build_analysis_rag_citations_merges_plan_and_business() -> None:
    p = RetrievedChunk(text="plan", doc_name="p1", namespace="nl2sql_schema")
    b = RetrievedChunk(text="biz", doc_name="b1", namespace="global")
    cites = AnalysisGraphRunner._build_analysis_rag_citations(plan_chunks=[p], business_chunks=[b])
    assert len(cites) == 1
    assert cites[0]["doc_name"] == "b1"
    assert cites[0]["namespace"] == "global"


def test_build_analysis_rag_citations_excludes_nl2sql_db_namespaces() -> None:
    schema = RetrievedChunk(text="t", doc_name="s1", namespace="nl2sql_schema")
    biz = RetrievedChunk(text="b", doc_name="b1", namespace="nl2sql_biz_knowledge")
    qa = RetrievedChunk(text="q", doc_name="q1", namespace="nl2sql_qa_examples")
    cites = AnalysisGraphRunner._build_analysis_rag_citations(
        plan_chunks=[schema, biz],
        business_chunks=[qa],
    )
    assert cites == []


def test_build_business_rag_recall_query_overheat_boost() -> None:
    q = AnalysisGraphRunner._build_business_rag_recall_query("昨日超温情况", "overheat_guidance")
    assert q.startswith("overheat_guidance 昨日超温情况")
    assert "规格材质" in q
    assert "蠕变" in q


def test_build_business_rag_recall_query_defect_ident_boost() -> None:
    q = AnalysisGraphRunner._build_business_rag_recall_query(
        "1号炉低温过热器A侧第2排缺陷处置",
        "img_diag_defect_ident",
    )
    assert q.startswith("img_diag_defect_ident")
    assert "打磨补焊" in q
    assert "处置案例" in q


def test_build_business_rag_rerank_query_defect_ident() -> None:
    q = AnalysisGraphRunner._build_business_rag_rerank_query(
        "缺陷识别", "img_diag_defect_ident"
    )
    assert q is not None
    assert "处置方案" in q


def test_build_business_rag_recall_query_leakage_burst_boost() -> None:
    q = AnalysisGraphRunner._build_business_rag_recall_query(
        "#2炉过热器泄爆原因",
        "img_diag_leakage_burst",
    )
    assert q.startswith("img_diag_leakage_burst")
    assert "历史事故案例" in q
    assert "爆管" in q


def test_build_business_rag_rerank_query_leakage_burst() -> None:
    q = AnalysisGraphRunner._build_business_rag_rerank_query(
        "泄爆溯源", "img_diag_leakage_burst"
    )
    assert q is not None
    assert "规程条文" in q


def test_build_business_rag_recall_query_other_type_unchanged() -> None:
    q = AnalysisGraphRunner._build_business_rag_recall_query("检修策略", "maintenance_strategy")
    assert q == "maintenance_strategy 检修策略"
    assert "规格材质" not in q


def test_build_business_rag_rerank_query_overheat_only() -> None:
    assert AnalysisGraphRunner._build_business_rag_rerank_query("昨日超温", "overheat_guidance") == (
        "锅炉管壁超温 规格材质 受热面 昨日超温"
    )
    assert AnalysisGraphRunner._build_business_rag_rerank_query("检修", "maintenance_strategy") is None
