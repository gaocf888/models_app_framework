"""原文预览 URL（稳定引用 + 问答代理路径）单测。"""

from __future__ import annotations

from urllib.parse import unquote

from app.rag.original_preview import (
    PROXY_PREVIEW_PATH,
    build_original_proxy_preview_url,
    enrich_rag_citations_with_preview,
    resolve_original_preview_url,
    stable_ref_from_minio_http_url,
    strip_rag_citation_preview_urls,
)


def test_resolve_http_url_passthrough() -> None:
    url = "https://cdn.example.com/a.pdf"
    assert resolve_original_preview_url(url) == url


def test_resolve_minio_ref_returns_chatbot_proxy_path() -> None:
    ref = "minio://rag-assets/rag-docs/参考规范/doc/v1.0.1/original.pdf"
    preview = resolve_original_preview_url(ref)
    assert preview is not None
    assert preview.startswith(PROXY_PREVIEW_PATH + "?ref=")
    assert "models-app-minio" not in preview
    assert "9000" not in preview
    # ref 完整保留在 query 中
    assert "minio" in unquote(preview)


def test_build_proxy_url_encodes_ref() -> None:
    ref = "minio://rag-assets/rag-docs/参考规范/a.pdf"
    url = build_original_proxy_preview_url(ref)
    assert url.startswith("/chatbot/documents/original/preview?ref=")
    assert "%E5%8F%82" in url or "参考" in unquote(url)


def test_stable_ref_from_internal_presigned() -> None:
    url = (
        "http://models-app-minio:9000/rag-assets/rag-docs/%E5%8F%82%E8%80%83%E8%A7%84%E8%8C%83/"
        "doc/v1.0.1/original.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
    )
    stable = stable_ref_from_minio_http_url(url)
    assert stable == "minio://rag-assets/rag-docs/参考规范/doc/v1.0.1/original.pdf"
    preview = resolve_original_preview_url(url)
    assert preview is not None
    assert preview.startswith(PROXY_PREVIEW_PATH)


def test_strip_preview_keeps_stable_ref() -> None:
    cites = [
        {
            "ref_index": 1,
            "original_content_url": "minio://rag-assets/rag-docs/ns/doc/v1/original.pdf",
            "original_preview_url": "/chatbot/documents/original/preview?ref=x",
            "doc_name": "doc",
        }
    ]
    out = strip_rag_citation_preview_urls(cites)
    assert out is not None
    assert out[0]["original_content_url"].startswith("minio://")
    assert "original_preview_url" not in out[0]
    assert out[0]["doc_name"] == "doc"


def test_enrich_adds_preview_for_http() -> None:
    cites = [{"ref_index": 1, "original_content_url": "https://cdn.example.com/a.pdf"}]
    out = enrich_rag_citations_with_preview(cites)
    assert out[0]["original_preview_url"] == "https://cdn.example.com/a.pdf"


def test_enrich_minio_uses_proxy_path() -> None:
    cites = [
        {
            "ref_index": 1,
            "original_content_url": "minio://rag-assets/rag-docs/ns/doc/v1/original.pdf",
        }
    ]
    out = enrich_rag_citations_with_preview(cites)
    assert out[0]["original_preview_url"].startswith(PROXY_PREVIEW_PATH)


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
                "original_preview_url": "/chatbot/documents/original/preview?ref=x",
            }
        ],
    )
    msgs = mgr.get_session_messages("u1", "s1")
    assert len(msgs) == 1
    rc = msgs[0].get("rag_citations") or []
    assert rc[0]["original_content_url"].startswith("minio://")
    assert "original_preview_url" not in rc[0]
