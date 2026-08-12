"""智能客服 HITL：意图路由确认与 NL2SQL 生成失败续跑。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.llm.graphs.chatbot_hitl import (
    ACTION_FALLBACK_KB_QA,
    ACTION_NL2SQL_RETRY,
    ACTION_PICK_DISAMBIGUATION_OPTION,
    ACTION_ROUTE_CLARIFY,
    ACTION_ROUTE_DATA_QUERY,
    ACTION_ROUTE_KB_QA,
    ChatbotHitlValidationError,
    apply_chatbot_hitl_action,
    build_nl2sql_retry_hint,
    prepare_intent_disambiguation_hitl_patch,
    prepare_intent_hitl_patch,
    should_trigger_intent_hitl,
    should_trigger_nl2sql_hitl,
)
from app.llm.graphs.chatbot_hitl_display import (
    HITL_KIND_INTENT_DISAMBIGUATION,
    HITL_KIND_INTENT_ROUTE,
    HITL_KIND_NL2SQL_GEN_FAILED,
    build_hitl_interrupt_payload,
)
from app.llm.graphs.chatbot_intent_disambiguation import (
    build_fallback_disambiguation,
    _coerce_disambiguation_result,
)


def _hitl_on(**overrides):
    cfg = {
        "hitl_enabled": True,
        "intent_hitl_enabled": True,
        "intent_hitl_min_confidence": 0.75,
        "intent_disambiguation_enabled": True,
        "intent_disambiguation_timeout_sec": 15.0,
        "intent_hitl_max_rounds": 2,
        "nl2sql_hitl_enabled": True,
        "nl2sql_hitl_max_retries": 1,
    }
    cfg.update(overrides)
    return patch(
        "app.llm.graphs.chatbot_hitl._cfg",
        return_value=type("C", (), cfg)(),
    )


from app.llm.graphs.chatbot_hitl_session_store import (
    create_chatbot_hitl_resume_session,
    delete_chatbot_hitl_resume_session,
    get_chatbot_hitl_resume_session,
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


def test_should_not_trigger_intent_hitl_clear_hybrid():
    """清晰强混合（hybrid_qa + mixed_hybrid）直走 Hybrid，不弹意图 HITL。"""
    state = {
        "intent_label": "hybrid_qa",
        "intent_confidence": 0.75,
        "intent_reason": "mixed_hybrid",
        "query": "查出超温列表并结合规程说明如何处置",
    }
    with _hitl_on():
        assert should_trigger_intent_hitl(state) is False


def test_should_trigger_intent_hitl_low_conf_hybrid():
    """低置信 hybrid 仍可弹 HITL（留给真模糊），并提供第四钮综合。"""
    state = {
        "intent_label": "hybrid_qa",
        "intent_confidence": 0.5,
        "intent_reason": "llm_classifier",
        "query": "超温相关",
    }
    with _hitl_on():
        assert should_trigger_intent_hitl(state) is True


def test_apply_route_hybrid_qa_sets_confirmed_route():
    from app.llm.graphs.chatbot_hitl import ACTION_ROUTE_HYBRID

    out = apply_chatbot_hitl_action(
        {"query": "查出超温并说明处置", "intent_label": "kb_qa"},
        action=ACTION_ROUTE_HYBRID,
    )
    assert out["confirmed_route"] == "hybrid_qa"
    assert out["intent_label"] == "hybrid_qa"


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


def test_prepare_intent_hitl_sets_round_one():
    patch_state = prepare_intent_hitl_patch({"query": "混合问句"})
    assert patch_state["hitl_kind"] == HITL_KIND_INTENT_ROUTE
    assert patch_state["intent_hitl_round"] == 1


def test_should_not_trigger_intent_hitl_when_round_at_max():
    state = {
        "intent_label": "kb_qa",
        "intent_confidence": 0.5,
        "intent_reason": "mixed_data_and_concept",
        "query": "为什么当前负荷是多少",
        "intent_hitl_round": 2,
    }
    with _hitl_on(intent_hitl_max_rounds=2):
        assert should_trigger_intent_hitl(state) is False


def test_fallback_disambiguation_covers_both_routes():
    fb = build_fallback_disambiguation(query="今天有多少超温数据，什么原因")
    assert len(fb["options"]) == 3
    routes = {o["route_hint"] for o in fb["options"]}
    assert "data_query" in routes and "kb_qa" in routes


def test_coerce_partial_llm_falls_back_to_cover_routes():
    out = _coerce_disambiguation_result(
        {
            "analysis": "边界不清",
            "options": [
                {"title": "只查数", "query": "今天超温条数？", "route_hint": "data_query"},
            ],
        },
        query="今天有多少超温数据，什么原因",
    )
    assert len(out["options"]) == 3
    routes = {o["route_hint"] for o in out["options"]}
    assert "data_query" in routes and "kb_qa" in routes


def test_prepare_disambiguation_hitl_patch():
    fb = build_fallback_disambiguation(query="混合问")
    patch_state = prepare_intent_disambiguation_hitl_patch(
        {"query": "混合问"},
        analysis=fb["analysis"],
        options=fb["options"],
        source="fallback_rules",
    )
    assert patch_state["hitl_kind"] == HITL_KIND_INTENT_DISAMBIGUATION
    assert patch_state["intent_hitl_round"] == 2
    assert len(patch_state["disambiguation_options"]) == 3
    assert patch_state["disambiguation_source"] == "fallback_rules"


def test_build_hitl_payload_intent_route_includes_hybrid_button():
    from app.llm.graphs.chatbot_hitl_display import ACTION_ROUTE_HYBRID, INTENT_ROUTE_BUTTONS

    assert any(b["id"] == ACTION_ROUTE_HYBRID for b in INTENT_ROUTE_BUTTONS)
    state = {
        "hitl_kind": HITL_KIND_INTENT_ROUTE,
        "query": "模糊问",
        "intent_label": "kb_qa",
        "intent_confidence": 0.5,
    }
    payload = build_hitl_interrupt_payload(state)
    ids = [b["id"] for b in payload["ui_buttons"]]
    assert "route_hybrid_qa" in ids
    assert "route_data_query" in ids
    assert "route_kb_qa" in ids
    assert "route_clarify" in ids


def test_fallback_disambiguation_includes_hybrid_option():
    fb = build_fallback_disambiguation(query="查出超温并说明原因")
    routes = [o["route_hint"] for o in fb["options"]]
    assert "data_query" in routes
    assert "kb_qa" in routes
    assert "hybrid_qa" in routes


def test_apply_pick_disambiguation_hybrid_route_hint():
    fb = build_fallback_disambiguation(query="混合问")
    hybrid_idx = next(i for i, o in enumerate(fb["options"]) if o["route_hint"] == "hybrid_qa")
    out = apply_chatbot_hitl_action(
        {
            "query": "混合问",
            "disambiguation_options": fb["options"],
            "intent_label": "kb_qa",
        },
        action=ACTION_PICK_DISAMBIGUATION_OPTION,
        payload={"option_index": hybrid_idx},
    )
    assert out["confirmed_route"] == "hybrid_qa"
    assert out["intent_label"] == "hybrid_qa"
    assert out["query"] == fb["options"][hybrid_idx]["query"]


def test_build_hitl_payload_disambiguation_strips_user_inquiry_phrase():
    from app.llm.graphs.chatbot_hitl_display import build_disambiguation_hitl_prompt

    prompt = build_disambiguation_hitl_prompt(
        analysis="用户询问今天超温数据的数量及原因，问题同时涉及数据查询和知识说明，需要进一步明确意图。"
    )
    assert "用户询问" not in prompt
    assert "明确意图" not in prompt
    assert "请点选" in prompt


def test_build_hitl_payload_disambiguation_dynamic_buttons():
    fb = build_fallback_disambiguation(query="混合问")
    state = {
        "hitl_kind": HITL_KIND_INTENT_DISAMBIGUATION,
        "disambiguation_analysis": fb["analysis"],
        "disambiguation_options": fb["options"],
        "disambiguation_source": "fallback_rules",
        "query": "混合问",
    }
    payload = build_hitl_interrupt_payload(state)
    assert payload["hitl_kind"] == HITL_KIND_INTENT_DISAMBIGUATION
    assert len(payload["ui_buttons"]) == 3
    assert payload["ui_buttons"][0]["id"] == "pick_disambiguation_0"
    assert payload["ui_buttons"][0]["id"] != "route_data_query"
    # label 为完整 query，供前端渲染链接
    assert payload["ui_buttons"][0]["label"] == fb["options"][0]["query"]
    assert "请点击下方按钮" not in payload["prompt"]
    assert "1. 【" not in payload["prompt"]
    assert "用户询问" not in payload["prompt"]
    assert "明确意图" not in payload["prompt"]
    assert "请点选" in payload["prompt"]
    assert payload["context"]["disambiguation_source"] == "fallback_rules"


def test_apply_pick_disambiguation_by_button_id():
    fb = build_fallback_disambiguation(query="混合问")
    out = apply_chatbot_hitl_action(
        {
            "query": "混合问",
            "disambiguation_options": fb["options"],
            "hitl_kind": HITL_KIND_INTENT_DISAMBIGUATION,
        },
        action="pick_disambiguation_0",
    )
    assert out["query"] == fb["options"][0]["query"]
    assert out["confirmed_route"] == fb["options"][0]["route_hint"]
    assert out["pending_hitl"] is False
    assert out["intent_prev_task_type"] == "after_intent_confirm"


def test_apply_pick_disambiguation_by_option_index():
    fb = build_fallback_disambiguation(query="混合问")
    out = apply_chatbot_hitl_action(
        {"query": "混合问", "disambiguation_options": fb["options"]},
        action=ACTION_PICK_DISAMBIGUATION_OPTION,
        payload={"option_index": 1},
    )
    assert out["query"] == fb["options"][1]["query"]
    assert out["confirmed_route"] == fb["options"][1]["route_hint"]


def test_apply_pick_disambiguation_invalid_index():
    with pytest.raises(ChatbotHitlValidationError, match="option_index"):
        apply_chatbot_hitl_action(
            {"disambiguation_options": []},
            action=ACTION_PICK_DISAMBIGUATION_OPTION,
            payload={"option_index": 0},
        )
