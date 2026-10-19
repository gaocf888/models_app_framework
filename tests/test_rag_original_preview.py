"""原文预览 URL（方案 A：稳定引用 + 短时 preview）单测。"""

from __future__ import annotations

from app.rag.original_preview import (
    enrich_rag_citations_with_preview,
    resolve_original_preview_url,
    strip_rag_citation_preview_urls,
)


def test_resolve_http_url_passthrough() -> None:
    url = "https://cdn.example.com/a.pdf"
    assert resolve_original_preview_url(url) == url


def test_strip_preview_keeps_stable_ref() -> None:
    cites = [
        {
            "ref_index": 1,
            "original_content_url": "minio://rag-assets/rag-docs/ns/doc/v1/original.pdf",
            "original_preview_url": "https://expired.example/x",
            "doc_name": "doc",
        }
    ]
    out = strip_rag_citation_preview_urls(cites)
    assert out is not None
    assert out[0]["original_content_url"].startswith("minio://")
    assert "original_preview_url" not in out[0]
    assert out[0]["doc_name"] == "doc"


def test_enrich_adds_preview_for_http(monkeypatch) -> None:
    cites = [{"ref_index": 1, "original_content_url": "https://cdn.example.com/a.pdf"}]
    out = enrich_rag_citations_with_preview(cites)
    assert out[0]["original_preview_url"] == "https://cdn.example.com/a.pdf"


def test_enrich_minio_uses_resolver(monkeypatch) -> None:
    from app.rag import original_preview as op

    monkeypatch.setattr(
        op,
        "resolve_original_preview_url",
        lambda ref: "https://presigned.example/doc.pdf",
    )
    cites = [
        {
            "ref_index": 1,
            "original_content_url": "minio://rag-assets/rag-docs/ns/doc/v1/original.pdf",
        }
    ]
    out = enrich_rag_citations_with_preview(cites)
    assert out[0]["original_preview_url"] == "https://presigned.example/doc.pdf"


def test_conversation_manager_strips_preview_on_persist() -> None:
    from app.conversation.manager import ConversationManager
    from app.conversation.store import ConversationStore

    store = ConversationStore()
    mgr = ConversationManager(store=store)
    mgr.append_assistant_message(
        "u1",
        "s1",
        "answer",
        rag_citations=[
            {
                "ref_index": 1,
                "original_content_url": "minio://rag-assets/rag-docs/ns/doc/v1/original.pdf",
                "original_preview_url": "https://should-not-persist.example/x",
            }
        ],
    )
    msgs = mgr.get_session_messages("u1", "s1")
    assert len(msgs) == 1
    rc = msgs[0].get("rag_citations") or []
    assert rc[0]["original_content_url"].startswith("minio://")
    assert "original_preview_url" not in rc[0]
