from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.conversation.message_id import build_conversation_message_id
from app.conversation.store import ConversationStore


class _FakeArchive:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.delete_msg_calls: list[tuple[str, str, str]] = []
        self.enabled = True
        self.fallback_enabled = False
        self.updated: list[tuple[str, str, str, bool]] = []
        self.allow_update_when_required = False
        self.cold_messages: list[dict] = []

    def delete_session(self, *, user_id: str, session_id: str) -> None:
        self.calls.append((user_id, session_id))

    def delete_message(self, *, user_id: str, session_id: str, message_id: str) -> bool:
        self.delete_msg_calls.append((user_id, session_id, message_id))
        return False

    def archive_message(self, **_: object) -> None:
        return

    def update_session_title(
        self,
        *,
        user_id: str,
        session_id: str,
        title: str,
        title_source: str = "user",
        require_existing: bool = False,
    ) -> bool:
        assert title_source == "user"
        self.updated.append((user_id, session_id, title, require_existing))
        if require_existing:
            return self.allow_update_when_required
        return True

    def list_messages(self, *, user_id: str, session_id: str, limit: int | None = None) -> list[dict]:
        _ = (user_id, session_id, limit)
        return list(self.cold_messages)


def test_clear_session_also_deletes_archive(monkeypatch):
    fake_archive = _FakeArchive()
    monkeypatch.setattr("app.conversation.manager.get_archive_store", lambda: fake_archive)

    store = ConversationStore()
    mgr = ConversationManager(store=store)
    mgr.append_user_message("u_1", "s_1", "hello")
    assert mgr.get_session_messages("u_1", "s_1")

    mgr.clear_session("u_1", "s_1")

    assert mgr.get_session_messages("u_1", "s_1") == []
    assert fake_archive.calls == [("u_1", "s_1")]


def test_update_title_can_fallback_to_cold_layer(monkeypatch):
    fake_archive = _FakeArchive()
    fake_archive.allow_update_when_required = True
    monkeypatch.setattr("app.conversation.manager.get_archive_store", lambda: fake_archive)

    # 空热层：模拟 Redis TTL 过期后仅冷层存在会话
    mgr = ConversationManager(store=ConversationStore())
    ok = mgr.update_session_title("u_1", "s_1", "new title")

    assert ok is True
    assert fake_archive.updated == [("u_1", "s_1", "new title", True)]


def test_get_session_messages_dedup_by_message_id(monkeypatch):
    fake_archive = _FakeArchive()
    fake_archive.fallback_enabled = True
    monkeypatch.setattr("app.conversation.manager.get_archive_store", lambda: fake_archive)

    store = ConversationStore()
    mgr = ConversationManager(store=store)
    mgr.append_user_message("u_1", "s_1", "hello")
    hot = mgr.get_session_messages("u_1", "s_1")
    assert len(hot) == 1
    hot_ts = float(hot[0]["ts"])
    # 冷层时间戳通常是毫秒精度还原后的秒值（精度低于热层）
    fake_archive.cold_messages = [{"role": "user", "content": "hello", "ts": int(hot_ts * 1000) / 1000.0}]

    merged = mgr.get_session_messages("u_1", "s_1")
    assert len(merged) == 1
    assert merged[0]["role"] == "user"
    assert merged[0]["content"] == "hello"


def test_delete_message_removes_hot_and_calls_archive(monkeypatch):
    fake_archive = _FakeArchive()
    monkeypatch.setattr("app.conversation.manager.get_archive_store", lambda: fake_archive)

    store = ConversationStore()
    mgr = ConversationManager(store=store)
    mgr.append_user_message("u_1", "s_1", "a")
    mgr.append_assistant_message("u_1", "s_1", "b")
    msgs = mgr.get_session_messages("u_1", "s_1")
    assert len(msgs) == 2
    mid0 = build_conversation_message_id(
        "u_1", "s_1", msgs[0]["role"], msgs[0]["content"], msgs[0].get("ts")
    )

    ok = mgr.delete_message("u_1", "s_1", mid0)
    assert ok is True
    rest = mgr.get_session_messages("u_1", "s_1")
    assert len(rest) == 1
    assert rest[0]["role"] == "assistant"
    assert fake_archive.delete_msg_calls == [("u_1", "s_1", mid0)]

