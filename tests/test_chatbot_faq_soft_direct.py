"""chatbot_faq_soft_direct 软直通判定单测。"""

from app.llm.graphs.chatbot_faq_soft_direct import (
    evaluate_faq_soft_direct,
    query_starts_with_anaphora_marker,
    snippets_for_llm_generation,
)


def _citations(score: float, preview: str = "问：A 答：B") -> list[dict]:
    return [{"score": score, "text_preview": preview, "doc_name": "锅炉运行与检修1000问"}]


def test_soft_direct_active_on_high_score_and_none_anaphora():
    d = evaluate_faq_soft_direct(
        enabled=True,
        min_score=0.95,
        enable_rag=True,
        intent_label="kb_qa",
        anaphora_type="none",
        anaphora_rule_type="none",
        query="燃烧管理系统有哪些功能",
        rag_citations=_citations(0.9999),
        context_snippets=["#807 燃烧管理系统主要功能…"],
    )
    assert d.active is True
    assert "active" in d.reason


def test_soft_direct_disabled_by_config():
    d = evaluate_faq_soft_direct(
        enabled=False,
        min_score=0.95,
        enable_rag=True,
        intent_label="kb_qa",
        anaphora_type="none",
        anaphora_rule_type="none",
        query="燃烧管理系统有哪些功能",
        rag_citations=_citations(0.99),
        context_snippets=["x"],
    )
    assert d.active is False
    assert d.reason == "disabled"


def test_soft_direct_blocked_by_meta_confirm_anaphora():
    d = evaluate_faq_soft_direct(
        enabled=True,
        min_score=0.95,
        enable_rag=True,
        intent_label="kb_qa",
        anaphora_type="meta_confirm",
        anaphora_rule_type="meta_confirm",
        query="确定吗",
        rag_citations=_citations(0.99),
        context_snippets=["x"],
    )
    assert d.active is False


def test_soft_direct_blocked_by_anaphora_query_prefix():
    assert query_starts_with_anaphora_marker("这个系统有哪些功能") is True
    d = evaluate_faq_soft_direct(
        enabled=True,
        min_score=0.95,
        enable_rag=True,
        intent_label="kb_qa",
        anaphora_type="none",
        anaphora_rule_type="none",
        query="这个系统有哪些功能",
        rag_citations=_citations(0.99),
        context_snippets=["x"],
    )
    assert d.active is False
    assert d.reason == "query_anaphora_prefix"


def test_soft_direct_blocked_by_low_score():
    d = evaluate_faq_soft_direct(
        enabled=True,
        min_score=0.95,
        enable_rag=True,
        intent_label="kb_qa",
        anaphora_type="none",
        anaphora_rule_type="none",
        query="燃烧管理系统有哪些功能",
        rag_citations=_citations(0.5),
        context_snippets=["x"],
    )
    assert d.active is False
    assert "score_below_threshold" in d.reason


def test_snippets_for_llm_top_n_when_soft_direct():
    items = ["a", "b", "c"]
    assert snippets_for_llm_generation(items, soft_direct=True, snippet_top_n=1) == ["a"]
    assert snippets_for_llm_generation(items, soft_direct=False, snippet_top_n=1) == ["a", "b", "c"]
