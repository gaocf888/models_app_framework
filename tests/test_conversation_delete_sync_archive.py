from __future__ import annotations

from app.conversation.manager import ConversationManager
from app.conversation.store import ConversationStore


class _FakeArchive:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.enabled = True
        self.fallback_enabled = False
        self.updated: list[tuple[str, str, str, bool]] = []
        self.allow_update_when_required = False

    def delete_session(self, *, user_id: str, session_id: str) -> None:
        self.calls.append((user_id, session_id))

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

