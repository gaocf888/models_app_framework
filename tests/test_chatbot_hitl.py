"""智能客服 HITL：意图路由确认与 NL2SQL 生成失败续跑。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.llm.graphs.chatbot_hitl import (
    ACTION_FALLBACK_KB_QA,
    ACTION_NL2SQL_RETRY,
    ACTION_ROUTE_CLARIFY,
    ACTION_ROUTE_DATA_QUERY,
    ACTION_ROUTE_KB_QA,
    ChatbotHitlValidationError,
    apply_chatbot_hitl_action,
    build_nl2sql_retry_hint,
    should_trigger_intent_hitl,
    should_trigger_nl2sql_hitl,
)
from app.llm.graphs.chatbot_hitl_display import HITL_KIND_INTENT_ROUTE, HITL_KIND_NL2SQL_GEN_FAILED
from app.llm.graphs.chatbot_hitl_session_store import (
    create_chatbot_hitl_resume_session,
    delete_chatbot_hitl_resume_session,
    get_chatbot_hitl_resume_session,
)


def _hitl_on(**overrides):
    cfg = {
        "hitl_enabled": True,
        "intent_hitl_enabled": True,
        "intent_hitl_min_confidence": 0.75,
        "nl2sql_hitl_enabled": True,
        "nl2sql_hitl_max_retries": 1,
    }
    cfg.update(overrides)
    return patch(
        "app.llm.graphs.chatbot_hitl._cfg",
        return_value=type("C", (), cfg)(),
    )


def test_should_trigger_intent_hitl_low_confidence():
    state = {
        "intent_label": "data_query",
        "intent_confidence": 0.5,
        "intent_reason": "llm",
        "query": "查一下1号炉负荷",
    }
    with _hitl_on():
        assert should_trigger_intent_hitl(state) is True


def test_should_not_trigger_intent_hitl_high_conf_structured():
    state = {
        "intent_label": "data_query",
        "intent_confidence": 0.85,
        "intent_reason": "structured_query_heuristic",
        "query": "查询台账记录",
    }
    with _hitl_on():
        assert should_trigger_intent_hitl(state) is False


def test_should_trigger_intent_hitl_mixed_reason():
    state = {
        "intent_label": "kb_qa",
        "intent_confidence": 0.8,
        "intent_reason": "mixed_data_and_concept",
        "query": "为什么当前负荷是多少",
    }
    with _hitl_on():
        assert should_trigger_intent_hitl(state) is True


def test_apply_route_data_query_sets_confirmed_route():
    out = apply_chatbot_hitl_action(
        {"query": "查负荷", "intent_label": "kb_qa"},
        action=ACTION_ROUTE_DATA_QUERY,
    )
    assert out["confirmed_route"] == "data_query"
    assert out["intent_label"] == "data_query"
    assert out["pending_hitl"] is False


def test_apply_nl2sql_retry_sets_skip_cache_and_hint():
    out = apply_chatbot_hitl_action(
        {
            "query": "查1号炉负荷",
            "nl2sql_fail_reason": "unknown columns: max_start_time",
            "nl2sql_retry_count": 0,
        },
        action=ACTION_NL2SQL_RETRY,
    )
    assert out["nl2sql_retry_count"] == 1
    assert out["nl2sql_skip_cache"] is True
    assert "max_start_time" in (out.get("nl2sql_retry_hint") or "")
    assert out["nl2sql_failed"] is False


def test_build_nl2sql_retry_hint_contains_reason():
    hint = build_nl2sql_retry_hint("unknown columns: foo")
    assert "unknown columns: foo" in hint
    assert "【上轮失败】" in hint


def test_should_trigger_nl2sql_hitl_respects_max_retries():
    state = {"nl2sql_retry_count": 1}
    with _hitl_on(nl2sql_hitl_max_retries=1):
        assert should_trigger_nl2sql_hitl(state, gen_failed=True) is False
    with _hitl_on(nl2sql_hitl_max_retries=2):
        assert should_trigger_nl2sql_hitl(state, gen_failed=True) is True


def test_apply_fallback_kb_qa_switches_route():
    out = apply_chatbot_hitl_action(
        {
            "query": "查负荷",
            "hitl_original_query": "查1号炉负荷",
            "used_nl2sql": True,
            "nl2sql_failed": True,
        },
        action=ACTION_FALLBACK_KB_QA,
    )
    assert out["confirmed_route"] == "kb_qa"
    assert out["intent_label"] == "kb_qa"
    assert out["used_nl2sql"] is False
    assert out["query"] == "查1号炉负荷"


def test_hitl_session_store_roundtrip():
    snap = {
        "query": "测试问句",
        "hitl_kind": HITL_KIND_INTENT_ROUTE,
        "intent_label": "data_query",
    }
    token = create_chatbot_hitl_resume_session(
        user_id="u1",
        session_id="s1",
        hitl_kind=HITL_KIND_INTENT_ROUTE,
        state_snapshot=snap,
        interrupt_payload={"prompt": "请选择", "ui_buttons": []},
    )
    sess = get_chatbot_hitl_resume_session(token)
    assert sess is not None
    assert sess.user_id == "u1"
    assert sess.state_snapshot["query"] == "测试问句"
    delete_chatbot_hitl_resume_session(token)
    assert get_chatbot_hitl_resume_session(token) is None


def test_apply_route_kb_qa_with_refined_query():
    out = apply_chatbot_hitl_action(
        {"query": "原问句"},
        action=ACTION_ROUTE_KB_QA,
        payload={"refined_query": "改写后的问句"},
    )
    assert out["query"] == "改写后的问句"


def test_apply_route_clarify_requires_refined_query():
    with pytest.raises(ChatbotHitlValidationError, match="refined_query is required"):
        apply_chatbot_hitl_action(
            {"query": "原问句", "intent_label": "kb_qa"},
            action=ACTION_ROUTE_CLARIFY,
        )


def test_apply_route_clarify_sets_query_and_clears_confirmed_route():
    out = apply_chatbot_hitl_action(
        {
            "query": "原问句",
            "intent_label": "kb_qa",
            "confirmed_route": "kb_qa",
        },
        action=ACTION_ROUTE_CLARIFY,
        payload={"refined_query": "  补充后的完整问句  "},
    )
    assert out["query"] == "补充后的完整问句"
    assert out.get("confirmed_route") == ""
    assert out["intent_prev_task_type"] == "after_intent_confirm"
    assert out.get("intent_label") != "clarify"


def test_chatbot_hitl_resume_request_route_clarify_requires_refined_query():
    from app.models.chatbot import ChatbotHitlResumeRequest

    with pytest.raises(ValueError, match="refined_query is required"):
        ChatbotHitlResumeRequest(
            user_id="u1",
            session_id="s1",
            resume_token="cb_rt_test",
            action="route_clarify",
            payload={},
        )


@pytest.mark.parametrize("action", [ACTION_ROUTE_DATA_QUERY, ACTION_ROUTE_KB_QA, ACTION_NL2SQL_RETRY])
def test_apply_hitl_clears_pending(action: str):
    out = apply_chatbot_hitl_action(
        {"pending_hitl": True, "nl2sql_fail_reason": "x", "nl2sql_retry_count": 0},
        action=action,
    )
    assert out["pending_hitl"] is False


def test_nl2sql_gen_failed_kind_constant():
    assert HITL_KIND_NL2SQL_GEN_FAILED == "nl2sql_gen_failed"
