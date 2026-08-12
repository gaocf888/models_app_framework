"""HITL 结构化落库：历史消息含 ui_buttons / resume_token / status。"""

from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.conversation.store import ConversationStore
from app.llm.graphs.chatbot_hitl_display import (
    HITL_KIND_INTENT_ROUTE,
    INTENT_ROUTE_BUTTONS,
    build_persisted_hitl,
)


def test_build_persisted_hitl_shape_excludes_disambiguation_options():
    hitl = build_persisted_hitl(
        hitl_kind=HITL_KIND_INTENT_ROUTE,
        ui_buttons=INTENT_ROUTE_BUTTONS,
        resume_token="tok_abc",
        status="pending",
    )
    assert hitl["hitl_kind"] == HITL_KIND_INTENT_ROUTE
    assert hitl["resume_token"] == "tok_abc"
    assert hitl["status"] == "pending"
    assert len(hitl["ui_buttons"]) == 4
    assert "disambiguation_options" not in hitl


def test_conversation_store_persists_and_resolves_hitl():
    store = ConversationStore()
    mgr = ConversationManager(store=store)
    uid, sid = "u_hitl", "s_hitl"
    token = "resume_token_1"
    hitl = build_persisted_hitl(
        hitl_kind=HITL_KIND_INTENT_ROUTE,
        ui_buttons=INTENT_ROUTE_BUTTONS,
        resume_token=token,
        status="pending",
    )
    mgr.append_user_message(uid, sid, "这个怎么办")
    mgr.append_assistant_message(uid, sid, "这个问题我还不够确定该怎么处理。请选择您希望我采用的方式：", hitl=hitl)

    msgs = mgr.get_session_messages(uid, sid)
    assert len(msgs) == 2
    assistant = msgs[-1]
    assert assistant["role"] == "assistant"
    assert isinstance(assistant.get("hitl"), dict)
    assert assistant["hitl"]["resume_token"] == token
    assert assistant["hitl"]["status"] == "pending"
    assert [b["id"] for b in assistant["hitl"]["ui_buttons"]] == [b["id"] for b in INTENT_ROUTE_BUTTONS]
    assert "disambiguation_options" not in assistant["hitl"]

    assert mgr.resolve_hitl_by_resume_token(uid, sid, token) is True
    msgs2 = mgr.get_session_messages(uid, sid)
    assert msgs2[-1]["hitl"]["status"] == "resolved"
    assert msgs2[-1]["hitl"]["resume_token"] == token


def test_session_message_hitl_api_normalizer():
    from app.llm.graphs.chatbot_hitl_display import normalize_persisted_hitl

    raw = {
        "hitl_kind": HITL_KIND_INTENT_ROUTE,
        "ui_buttons": [{"id": "route_kb_qa", "label": "基于知识库分析"}],
        "resume_token": "t1",
        "status": "pending",
        "disambiguation_options": [{"query": "should not leak"}],
    }
    out = normalize_persisted_hitl(raw)
    assert out is not None
    assert "disambiguation_options" not in out
    assert out["ui_buttons"][0]["id"] == "route_kb_qa"
    assert normalize_persisted_hitl(None) is None
