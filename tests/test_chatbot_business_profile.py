# -*- coding: utf-8 -*-
"""Chatbot business domain profile + P0 wiring smoke tests."""

from __future__ import annotations

import os

import pytest

from app.llm.graphs.chatbot_business_profile import (
    clear_chatbot_business_profile_cache,
    get_chatbot_business_profile,
    normalize_chatbot_domain,
)
from app.llm.graphs.chatbot_intent_rules import classify_chatbot_intent_by_rules
from app.llm.graphs.chatbot_nl2sql_answer import strip_sql_fences_from_analysis
from app.llm.graphs.chatbot_rag_citations import filter_rag_citation_dicts
from app.llm.graphs.chatbot_rag_scope import resolve_rag_namespace
from app.llm.graphs.chatbot_similar_cases import fault_keyword_match


@pytest.fixture(autouse=True)
def _clear_profile_cache():
    clear_chatbot_business_profile_cache()
    yield
    clear_chatbot_business_profile_cache()
    os.environ.pop("CHATBOT_DOMAIN", None)


def test_normalize_domain_aliases():
    assert normalize_chatbot_domain("boiler_four_tube") == "boiler"
    assert normalize_chatbot_domain("subsidence") == "subsidence"
    # 地降所主部署：未配置时默认地面沉降
    assert normalize_chatbot_domain("") == "subsidence"


def test_boiler_profile_loads_markers_and_texts():
    os.environ["CHATBOT_DOMAIN"] = "boiler"
    clear_chatbot_business_profile_cache()
    p = get_chatbot_business_profile()
    assert p.domain == "boiler"
    assert p.locale_kb.enabled is True
    assert "本厂" in p.locale_kb.markers
    assert len(p.data_markers) >= 10
    assert "请补充更具体的信息" in p.clarify_text


def test_subsidence_profile_locale_disabled_and_markers():
    os.environ["CHATBOT_DOMAIN"] = "subsidence"
    clear_chatbot_business_profile_cache()
    p = get_chatbot_business_profile()
    assert p.domain == "subsidence"
    assert p.locale_kb.enabled is False
    assert "本市" in p.locale_kb.markers
    assert "本厂" not in p.locale_kb.markers
    assert "累计沉降" in p.data_markers
    assert p.similar_case.gate_markers == ()


def test_intent_rules_subsidence_data_and_kb(monkeypatch):
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    r = classify_chatbot_intent_by_rules(
        "查询通州区近三个月累计沉降较大的分层标站点有哪些？",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "data_query"

    r2 = classify_chatbot_intent_by_rules(
        "地面沉降的成因机理是什么？",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r2.intent_label == "kb_qa"

    # 实体名词单独出现不应触发 data_query / hybrid_qa
    r3 = classify_chatbot_intent_by_rules(
        "分层标与基岩标监测差异是什么？",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r3.intent_label == "kb_qa"


def test_intent_rules_boiler_still_works(monkeypatch):
    monkeypatch.setenv("CHATBOT_DOMAIN", "boiler")
    clear_chatbot_business_profile_cache()
    r = classify_chatbot_intent_by_rules(
        "查询台账里1号炉最近一次检修记录",
        enable_nl2sql_route=True,
        image_urls=[],
    )
    assert r.intent_label == "data_query"


def test_locale_scope_subsidence_disabled(monkeypatch):
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    p = get_chatbot_business_profile()
    scope = resolve_rag_namespace(
        "本市沉降规范有哪些",
        enabled=p.locale_kb.enabled,
        plant_kb_namespace=p.locale_kb.namespace,
        query_boost_name=p.locale_kb.query_boost,
        locale_markers=p.locale_kb.markers,
    )
    assert scope.rag_namespace is None
    assert scope.rag_scope_reason == "locale_kb_disabled"


def test_fault_gate_disabled_for_subsidence(monkeypatch):
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    assert fault_keyword_match("锅炉爆管") is False


def test_strip_sql_fences_from_analysis():
    text = "结论如下。\n```sql\nSELECT 1;\n```\n后续分析。"
    out = strip_sql_fences_from_analysis(text)
    assert "SELECT" not in out
    assert "结论如下" in out


def test_filter_rag_citations_excludes_nl2sql_namespaces():
    cites = [
        {"ref_index": 1, "namespace": "regs", "doc_name": "a"},
        {"ref_index": 2, "namespace": "nl2sql_schema", "doc_name": "b"},
        {"ref_index": 3, "namespace": "nl2sql_biz_knowledge", "doc_name": "c"},
        {"ref_index": 4, "namespace": "nl2sql_qa_examples", "doc_name": "d"},
    ]
    filtered = filter_rag_citation_dicts(cites)
    assert len(filtered) == 1
    assert filtered[0]["namespace"] == "regs"


def test_build_finished_meta_data_query_keeps_filtered_citations():
    import time

    from app.llm.graphs.chatbot_graph_runner import ChatbotLangGraphRunner

    runner = object.__new__(ChatbotLangGraphRunner)
    runner._similar_case_namespace = "事故案例"
    runner._anaphora_expose_meta = False
    state = {
        "intent_label": "data_query",
        "used_rag": True,
        "retrieval_attempts": 1,
        "rag_engine": "hybrid",
        "rag_namespace": "regs",
        "rag_scope_reason": "",
        "rag_scope_fallback": False,
        "faq_soft_direct": False,
        "faq_soft_direct_reason": "",
        "history_trim_dropped": 0,
        "status": "success",
        "terminate_reason": None,
        "is_partial": False,
        "similar_cases_appended": False,
        "fault_detect_sources": [],
        "fault_detect_confidence": 0.0,
        "need_similar_cases": False,
        "used_nl2sql": True,
        "nl2sql_failed": False,
        "nl2sql_error_code": None,
        "nl2sql_sql": "SELECT 1",
        "nl2sql_analysis": {"empty": False},
        "suggested_questions": ["应被清空"],
        "rag_citations": [
            {"ref_index": 1, "namespace": "regs", "doc_name": "规范A"},
            {"ref_index": 2, "namespace": "nl2sql_schema", "doc_name": "schema"},
        ],
        "hybrid_degraded": None,
        "image_urls": [],
        "original_image_urls": [],
        "trace_request_id": "req-1",
    }
    meta = ChatbotLangGraphRunner._build_finished_meta(runner, state, time.perf_counter(), "sid-1")
    assert meta["suggested_questions"] == []
    assert len(meta["rag_citations"]) == 1
    assert meta["rag_citations"][0]["namespace"] == "regs"
    assert meta["used_nl2sql"] is True
    assert meta["nl2sql_sql"] == "SELECT 1"


def test_hard_gates_and_history_markers_from_domain(monkeypatch):
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    p = get_chatbot_business_profile()
    assert p.unclear_patterns
    assert "沉降" in p.history_continuation_markers
    assert "炉" not in p.history_continuation_markers

    cold = classify_chatbot_intent_by_rules(
        "怎么办",
        enable_nl2sql_route=True,
        image_urls=[],
        history_messages=[],
    )
    assert cold.intent_label == "clarify"

    continued = classify_chatbot_intent_by_rules(
        "怎么办",
        enable_nl2sql_route=True,
        image_urls=[],
        history_messages=[
            {"role": "user", "content": "分层标与基岩标监测差异是什么"},
            {"role": "assistant", "content": "分层标侧重分层变形，基岩标侧重区域基准。"},
        ],
    )
    assert continued.intent_label == "kb_qa"


def test_follow_up_topics_from_domain(monkeypatch):
    from app.llm.graphs.chatbot_follow_up import _rule_based_suggestions

    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    p = get_chatbot_business_profile()
    assert "沉降" in p.topic_follow_ups
    assert "地面沉降" in p.follow_up_llm_system or "沉降" in p.follow_up_llm_system
    qs = _rule_based_suggestions("通州区沉降偏大怎么办", 3)
    assert qs
    assert any("沉降" in q or "站点" in q or "规范" in q for q in qs)


def test_rag_admin_sidebar_nl2sql_exclude_constant():
    from app.rag.namespace_kb import NL2SQL_KB_ADMIN_SIDEBAR_EXCLUDED_NAMESPACES

    assert "nl2sql_schema" in NL2SQL_KB_ADMIN_SIDEBAR_EXCLUDED_NAMESPACES
    assert "nl2sql_biz_knowledge" in NL2SQL_KB_ADMIN_SIDEBAR_EXCLUDED_NAMESPACES
    assert "nl2sql_qa_examples" in NL2SQL_KB_ADMIN_SIDEBAR_EXCLUDED_NAMESPACES


def test_chatbot_retrieve_excludes_nl2sql_and_generation_texts(monkeypatch):
    from app.llm.graphs.chatbot_business_profile import get_chatbot_retrieve_exclude_namespaces

    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    p = get_chatbot_business_profile()
    assert "nl2sql_schema" in get_chatbot_retrieve_exclude_namespaces()
    assert "nl2sql_qa_examples" in get_chatbot_retrieve_exclude_namespaces()
    assert "知识库暂无足够依据" in p.insufficient_evidence_hint
    assert "燃烧优化" not in p.faq_soft_direct_authority


def test_faq_soft_direct_authority_domainized(monkeypatch):
    from app.llm.graphs.chatbot_faq_soft_direct import (
        _active_faq_authority_block,
        evaluate_faq_soft_direct,
        format_rag_snippets_for_generation,
    )

    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    block = _active_faq_authority_block()
    assert "成因概述" in block or "权威片段" in block
    assert "燃烧优化" not in block

    d = evaluate_faq_soft_direct(
        enabled=True,
        min_score=0.9,
        enable_rag=True,
        intent_label="data_query",
        anaphora_type="none",
        anaphora_rule_type="none",
        query="通州区年沉降如何解读",
        rag_citations=[{"rerank_score": 0.99, "text_preview": "答：…"}],
        context_snippets=["[1] 片段"],
    )
    assert d.active is False
    assert "intent_not_kb_qa" in d.reason

    out = format_rag_snippets_for_generation(
        ["[1] 规范条文"],
        soft_direct=True,
        base_formatter=lambda xs: "BODY",
    )
    assert "权威片段" in out
    assert out.endswith("BODY")


def test_rag_snippet_block_insufficient_evidence_wording(monkeypatch):
    from app.llm.graphs.chatbot_retrieval_query import format_rag_snippets_system_block

    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    text = format_rag_snippets_system_block(["[1] 分层标说明\n正文"])
    assert "知识库暂无足够依据" in text
    assert "监测指标" in text or "规范条款" in text
    assert "故障类型" not in text


def test_hybrid_texts_subsidence_domain(monkeypatch):
    monkeypatch.setenv("CHATBOT_DOMAIN", "subsidence")
    clear_chatbot_business_profile_cache()
    p = get_chatbot_business_profile()
    assert "监测查询结果" in p.hybrid_query_result_title
    assert "规范" in p.hybrid_kb_title or "知识" in p.hybrid_kb_title
    assert "沉降" in p.hybrid_synth_constraint or "下沉" in p.hybrid_synth_constraint
    assert "台账" not in p.hybrid_both_failed
    assert "锅炉" not in p.hybrid_both_failed


@pytest.mark.asyncio
async def test_data_query_kb_light_writes_citations_and_keeps_answer():
    from unittest.mock import AsyncMock, patch

    from app.llm.graphs.chatbot_graph_runner import ChatbotLangGraphRunner

    runner = object.__new__(ChatbotLangGraphRunner)
    runner._merge_graph_state = ChatbotLangGraphRunner._merge_graph_state
    runner._node_select_rag_engine = AsyncMock(return_value={"rag_engine": "hybrid"})
    runner._node_rag_scope_resolve = AsyncMock(
        return_value={"rag_namespace": "regs", "rag_scope_reason": "default"}
    )
    runner._node_kb_retrieve = AsyncMock(
        return_value={
            "rag_citations": [
                {"ref_index": 1, "namespace": "regs", "doc_name": "规范A"},
                {"ref_index": 2, "namespace": "nl2sql_schema", "doc_name": "schema"},
            ],
            "context_snippets": ["[1] a", "[2] b", "[3] c", "[4] d"],
            "retrieval_score": 0.8,
            "retrieval_attempts": 1,
            "rag_scope_fallback": False,
        }
    )

    state = {
        "enable_rag": True,
        "answer_text": "查数主答保持不变",
        "query": "通州区年沉降",
        "rag_scope_reason": "",
    }
    with patch("app.llm.graphs.chatbot_graph_runner.logger"):
        patch_out = await ChatbotLangGraphRunner._node_data_query_kb_light(runner, state)

    assert patch_out["used_rag"] is True
    assert len(patch_out["rag_citations"]) == 1
    assert patch_out["rag_citations"][0]["namespace"] == "regs"
    assert len(patch_out["context_snippets"]) == 3
    assert "data_query_kb_light" in patch_out["rag_scope_reason"]
    assert "answer_text" not in patch_out


@pytest.mark.asyncio
async def test_data_query_kb_light_skips_when_rag_disabled():
    from app.llm.graphs.chatbot_graph_runner import ChatbotLangGraphRunner

    runner = object.__new__(ChatbotLangGraphRunner)
    out = await ChatbotLangGraphRunner._node_data_query_kb_light(
        runner, {"enable_rag": False, "answer_text": "主答"}
    )
    assert out["used_rag"] is False
    assert out["rag_citations"] == []
