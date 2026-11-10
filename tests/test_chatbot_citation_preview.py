"""知识引用预览页与 preview_url 字段单测。"""

from app.llm.graphs.chatbot_citation_preview import (
    load_citation_preview,
    render_citation_preview_html,
)
from app.llm.graphs.chatbot_rag_citations import (
    build_citation_preview_url,
    chunks_to_rag_citations,
)
from app.rag.models import RetrievedChunk


class _FakeStore:
    def __init__(self, rows_by_doc: dict[str, list[dict]], by_id: dict[str, dict]):
        self._rows_by_doc = rows_by_doc
        self._by_id = by_id

    def get_chunks_by_ext_ids(self, ext_ids, namespace=None):
        out = []
        for i in ext_ids:
            row = self._by_id.get(str(i))
            if not row:
                continue
            if namespace is not None and row.get("namespace") != namespace:
                continue
            out.append(row)
        return out

    def list_chunks_for_document(self, doc_name, namespace=None, doc_version=None, limit=50000):
        rows = list(self._rows_by_doc.get(doc_name, []))
        if namespace is not None:
            rows = [r for r in rows if r.get("namespace") == namespace]
        return rows[:limit]


def test_build_citation_preview_url_shape():
    url = build_citation_preview_url(
        chunk_id="c-1",
        namespace="boiler",
        doc_name="手册.pdf",
        doc_version="v1",
        ref_index=2,
    )
    assert url is not None
    assert url.startswith("/chatbot/citation-preview?")
    assert "chunk_id=c-1" in url
    assert "namespace=boiler" in url
    assert "ref_index=2" in url
    assert build_citation_preview_url() is None


def test_chunks_to_rag_citations_includes_preview_url():
    cites = chunks_to_rag_citations(
        [
            RetrievedChunk(
                text="低压低温再热器说明" * 5,
                doc_name="锅炉说明书",
                namespace="ns1",
                chunk_id="cid-9",
                doc_version="v2",
                metadata={"source_uri": "https://cdn.example.com/a.pdf"},
            )
        ]
    )
    assert cites[0]["original_content_url"] == "https://cdn.example.com/a.pdf"
    assert "text_preview" in cites[0]
    assert cites[0]["preview_url"].startswith("/chatbot/citation-preview?")
    assert "chunk_id=cid-9" in cites[0]["preview_url"]
    assert "ref_index=1" in cites[0]["preview_url"]


def test_load_and_render_highlights_target_chunk():
    rows = [
        {
            "text": "第一段绪论",
            "ext_id": "a1",
            "namespace": "ns",
            "doc_name": "手册",
            "metadata": {"chunk_index": 0, "section_path": "1 绪论"},
        },
        {
            "text": "目标知识：低压低温再热器布置",
            "ext_id": "a2",
            "namespace": "ns",
            "doc_name": "手册",
            "metadata": {
                "chunk_index": 1,
                "section_path": "2 再热器",
                "source_uri": "https://cdn.example.com/manual.pdf",
            },
        },
        {
            "text": "第三段附录",
            "ext_id": "a3",
            "namespace": "ns",
            "doc_name": "手册",
            "metadata": {"chunk_index": 2},
        },
    ]
    store = _FakeStore({"手册": rows}, {r["ext_id"]: r for r in rows})
    model = load_citation_preview(
        chunk_id="a2",
        namespace="ns",
        doc_name="手册",
        ref_index=1,
        store=store,
    )
    assert model.target_chunk_id == "a2"
    assert any(c.is_target and "低压低温再热器" in c.text for c in model.chunks)
    assert model.original_content_url == "https://cdn.example.com/manual.pdf"
    html = render_citation_preview_html(model)
    assert 'id="cite-target"' in html
    assert "本轮引用" in html
    assert "低压低温再热器布置" in html
    assert "打开原文档" in html
