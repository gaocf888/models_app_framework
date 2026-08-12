"""partial 结构化落库：content 无 [partial] 前缀，is_partial 字段标记。"""

from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.conversation.message_id import build_conversation_message_id
from app.conversation.store import ConversationStore, strip_legacy_partial_prefix


def test_strip_legacy_partial_prefix():
    assert strip_legacy_partial_prefix("[partial] 已生成片段") == ("已生成片段", True)
    assert strip_legacy_partial_prefix("[partial]已生成") == ("已生成", True)
    assert strip_legacy_partial_prefix("完整回复") == ("完整回复", False)


def test_append_partial_stores_flag_without_prefix():
    store = ConversationStore()
    mgr = ConversationManager(store=store)
    uid, sid = "u_partial", "s_partial"
    mgr.append_user_message(uid, sid, "请分析")
    mgr.append_assistant_message(uid, sid, "这是半截回复", is_partial=True)

    raw = mgr.get_session_messages(uid, sid)
    assert len(raw) == 2
    assistant = raw[-1]
    assert assistant["content"] == "这是半截回复"
    assert assistant.get("is_partial") is True
    assert not str(assistant["content"]).startswith("[partial]")

    hist = mgr.get_recent_history(uid, sid, limit=10)
    assert hist[-1]["content"] == "这是半截回复"
    assert hist[-1].get("is_partial") is True


def test_legacy_prefix_stripped_for_context_but_raw_kept_for_message_id():
    store = ConversationStore()
    mgr = ConversationManager(store=store)
    uid, sid = "u_legacy", "s_legacy"
    legacy = "[partial] 旧版半截"
    store.append_message(uid, sid, role="assistant", content=legacy)

    raw = mgr.get_session_messages(uid, sid)
    assert raw[0]["content"] == legacy
    mid = build_conversation_message_id(uid, sid, "assistant", legacy, raw[0].get("ts"))

    hist = mgr.get_recent_history(uid, sid, limit=1)
    assert hist[0]["content"] == "旧版半截"
    assert hist[0].get("is_partial") is True

    # 删除仍按原始 content 计算的 message_id
    assert mgr.delete_message(uid, sid, mid) is True
    assert mgr.get_session_messages(uid, sid) == []
