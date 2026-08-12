"""首轮意图 HITL：LLM 路线按钮筛选。"""

from __future__ import annotations

from app.llm.graphs.chatbot_hitl_display import (
    ACTION_ROUTE_CLARIFY,
    ACTION_ROUTE_DATA_QUERY,
    ACTION_ROUTE_HYBRID,
    ACTION_ROUTE_KB_QA,
    HITL_KIND_INTENT_ROUTE,
    INTENT_ROUTE_BUTTONS,
    build_hitl_interrupt_payload,
)
from app.llm.graphs.chatbot_intent_route_suggest import (
    build_fallback_route_buttons,
    coerce_routes_to_ui_buttons,
)


def test_coerce_routes_kb_only_keeps_clarify():
    buttons = coerce_routes_to_ui_buttons(["kb_qa"])
    assert buttons is not None
    ids = [b["id"] for b in buttons]
    assert ids == [ACTION_ROUTE_KB_QA, ACTION_ROUTE_CLARIFY]
    assert all("label" in b for b in buttons)


def test_coerce_routes_preserves_canonical_order():
    buttons = coerce_routes_to_ui_buttons(["hybrid_qa", "data_query", "kb_qa"])
    assert buttons is not None
    assert [b["id"] for b in buttons] == [
        ACTION_ROUTE_DATA_QUERY,
        ACTION_ROUTE_KB_QA,
        ACTION_ROUTE_HYBRID,
        ACTION_ROUTE_CLARIFY,
    ]


def test_coerce_routes_invalid_falls_back_none():
    assert coerce_routes_to_ui_buttons([]) is None
    assert coerce_routes_to_ui_buttons(["clarify"]) is None
    assert coerce_routes_to_ui_buttons(["foo"]) is None


def test_fallback_buttons_match_full_pool():
    fb = build_fallback_route_buttons()
    assert [b["id"] for b in fb] == [b["id"] for b in INTENT_ROUTE_BUTTONS]


def test_interrupt_payload_uses_custom_ui_buttons_same_shape():
    custom = coerce_routes_to_ui_buttons(["kb_qa", "hybrid_qa"])
    assert custom is not None
    payload = build_hitl_interrupt_payload(
        {
            "hitl_kind": HITL_KIND_INTENT_ROUTE,
            "query": "这个怎么办",
            "intent_label": "kb_qa",
            "intent_hitl_ui_buttons": custom,
            "intent_route_suggest_source": "llm",
        }
    )
    assert payload["hitl_kind"] == HITL_KIND_INTENT_ROUTE
    assert "prompt" in payload
    assert isinstance(payload["ui_buttons"], list)
    assert [b["id"] for b in payload["ui_buttons"]] == [
        ACTION_ROUTE_KB_QA,
        ACTION_ROUTE_HYBRID,
        ACTION_ROUTE_CLARIFY,
    ]
    assert all(set(b.keys()) >= {"id", "label"} for b in payload["ui_buttons"])
    assert payload["context"]["intent_route_suggest_source"] == "llm"
