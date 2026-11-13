"""知识引用预览页与 preview_url 字段单测。"""

from app.llm.graphs.chatbot_citation_preview import (
    browser_openable_original_url,
    load_citation_preview,
    render_citation_preview_html,
)
from app.llm.graphs.chatbot_citation_preview_pdf import (
    build_pdf_search_queries,
    looks_like_pdf_url,
)
from app.llm.graphs.chatbot_rag_citations import (
    build_citation_preview_url,
    chunks_to_rag_citations,
    ensure_citation_preview_urls,
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
        highlight="# 807. 燃烧管理系统（BMS）主要功能有哪些？",
    )
    assert url is not None
    assert url.startswith("/chatbot/citation-preview?")
    assert "chunk_id=c-1" in url
    assert "namespace=boiler" in url
    assert "ref_index=2" in url
    assert "highlight=" in url
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
    assert "highlight=" in cites[0]["preview_url"]


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
    assert model.preview_mode == "pdf"
    # 强制文本模式：验证分片高亮仍可用
    html = render_citation_preview_html(
        model,
        view="text",
        request_query="chunk_id=a2&namespace=ns&doc_name=%E6%89%8B%E5%86%8C",
    )
    assert 'id="cite-target"' in html
    assert "本轮引用" in html
    assert "低压低温再热器布置" in html
    assert "打开原文档" in html
    assert "PDF 原件预览" in html


def test_no_silent_fallback_to_preface_when_id_missing():
    """旧逻辑在未命中时高亮第 0 条「前言」；现应明确未定位且不高亮前言。"""
    rows = [
        {
            "text": "前言",
            "ext_id": "preface",
            "namespace": "boiler_knowledge",
            "doc_name": "锅炉运行与检修1000问",
            "metadata": {"chunk_index": 0},
        },
        {
            "text": "第一章 基础知识 ...... 1",
            "ext_id": "toc",
            "namespace": "boiler_knowledge",
            "doc_name": "锅炉运行与检修1000问",
            "metadata": {"chunk_index": 1},
        },
    ]
    store = _FakeStore({"锅炉运行与检修1000问": rows}, {})
    model = load_citation_preview(
        chunk_id="ba691b28-7782-4b11-92ad-ee4faa600345",
        namespace="boiler_knowledge",
        doc_name="锅炉运行与检修1000问",
        doc_version="v1",
        ref_index=1,
        store=store,
    )
    assert not any(c.is_target for c in model.chunks)
    assert model.message and "未能精确定位" in model.message
    html = render_citation_preview_html(model)
    assert 'id="cite-target"' not in html


def test_locate_by_highlight_when_chunk_id_not_in_window():
    """模拟 400 窗口截断：列表无目标 id，但 highlight/text_preview 可命中正文。"""
    bms = (
        "# 807. 燃烧管理系统（BMS）主要功能有哪些？ 答：燃烧管理系统（BMS）主要功能如下：\n\n"
        "(1) 点火前炉膛吹扫。(2) 油燃烧器自动控制。"
    )
    rows = [
        {
            "text": "前言",
            "ext_id": "preface",
            "namespace": "boiler_knowledge",
            "doc_name": "锅炉运行与检修1000问",
            "metadata": {"chunk_index": 0},
        },
        {
            "text": bms,
            "ext_id": "other-id-not-matching-url",
            "namespace": "boiler_knowledge",
            "doc_name": "锅炉运行与检修1000问",
            "metadata": {"chunk_index": 807},
        },
    ]
    store = _FakeStore({"锅炉运行与检修1000问": rows}, {})
    model = load_citation_preview(
        chunk_id="ba691b28-7782-4b11-92ad-ee4faa600345",
        namespace="boiler_knowledge",
        doc_name="锅炉运行与检修1000问",
        ref_index=1,
        highlight=bms[:120],
        store=store,
    )
    assert any(c.is_target and "燃烧管理系统" in c.text for c in model.chunks)
    assert not any(c.is_target and c.text.strip() == "前言" for c in model.chunks)
    html = render_citation_preview_html(model)
    assert "本轮引用" in html
    assert "燃烧管理系统" in html


def test_prepend_target_row_when_list_window_misses_id():
    """mget/回查能拿到目标，但 list 窗口不含该 id 时，应插入并高亮目标而非前言。"""
    bms = "# 807. 燃烧管理系统（BMS）主要功能有哪些？ 答：……"
    target = {
        "text": bms,
        "ext_id": "ba691b28-7782-4b11-92ad-ee4faa600345",
        "namespace": "boiler_knowledge",
        "doc_name": "锅炉运行与检修1000问",
        "metadata": {"chunk_index": 807},
    }
    rows = [
        {
            "text": "前言",
            "ext_id": "preface",
            "namespace": "boiler_knowledge",
            "doc_name": "锅炉运行与检修1000问",
            "metadata": {"chunk_index": 0},
        },
    ]
    store = _FakeStore(
        {"锅炉运行与检修1000问": rows},
        {target["ext_id"]: target},
    )
    model = load_citation_preview(
        chunk_id=target["ext_id"],
        namespace="boiler_knowledge",
        doc_name="锅炉运行与检修1000问",
        ref_index=1,
        store=store,
    )
    assert any(c.is_target and "BMS" in c.text for c in model.chunks)
    assert not any(c.is_target and c.text.strip() == "前言" for c in model.chunks)


def test_looks_like_pdf_url_and_search_queries():
    assert looks_like_pdf_url("https://cdn.example.com/a/boiler.pdf")
    assert looks_like_pdf_url("https://cdn.example.com/obj?filename=a.pdf")
    assert not looks_like_pdf_url("https://cdn.example.com/a.docx")
    qs = build_pdf_search_queries("# 1.2 型式\n超临界参数、变压运行直流炉")
    assert qs
    assert any("型式" in q for q in qs)


def test_pdf_preview_mode_renders_pdfjs_viewer():
    rows = [
        {
            "text": "1.2 型式\n本锅炉为超临界参数、变压运行直流炉。",
            "ext_id": "c5ba",
            "namespace": "Power_plant_knowledge",
            "doc_name": "锅炉说明书",
            "metadata": {
                "chunk_index": 2,
                "source_uri": "https://files.example.com/boiler-manual.pdf",
                "content_fetch_content_type": "application/pdf",
            },
        }
    ]
    store = _FakeStore({"锅炉说明书": rows}, {r["ext_id"]: r for r in rows})
    model = load_citation_preview(
        chunk_id="c5ba",
        namespace="Power_plant_knowledge",
        doc_name="锅炉说明书",
        ref_index=1,
        highlight="1.2 型式 超临界参数",
        store=store,
    )
    assert model.preview_mode == "pdf"
    assert model.pdf_search_queries
    html = render_citation_preview_html(
        model,
        request_query="chunk_id=c5ba&namespace=Power_plant_knowledge&doc_name=%E9%94%85%E7%82%89%E8%AF%B4%E6%98%8E%E4%B9%A6&api_key=k",
    )
    assert "pdf.min.js" in html
    assert "citation-preview/pdf-proxy" in html
    assert "解析文本预览" in html
    assert "view=text" in html


def test_docx_stays_on_text_preview():
    rows = [
        {
            "text": "Word 文档片段内容足够长用于预览",
            "ext_id": "w1",
            "namespace": "ns",
            "doc_name": "说明.docx",
            "metadata": {
                "chunk_index": 0,
                "source_uri": "https://files.example.com/说明.docx",
            },
        }
    ]
    store = _FakeStore({"说明.docx": rows}, {r["ext_id"]: r for r in rows})
    model = load_citation_preview(
        chunk_id="w1",
        namespace="ns",
        doc_name="说明.docx",
        store=store,
    )
    assert model.preview_mode == "text"
    html = render_citation_preview_html(model)
    assert "pdf.min.js" not in html
    assert "本轮引用" in html


def test_scanned_pdf_defaults_to_text_preview_keeps_pdf_entry():
    """扫描件（MinerU）默认解析文本，避免整本大文件；仍保留 PDF 原件入口。"""
    rows = [
        {
            "text": "扫描件解析出的 BMS 功能说明段落足够长用于定位",
            "ext_id": "scan-1",
            "namespace": "boiler_knowledge",
            "doc_name": "锅炉1000问.pdf",
            "metadata": {
                "chunk_index": 10,
                "source_uri": "https://files.example.com/boiler-1000q-scan.pdf",
                "content_fetch_content_type": "application/pdf",
                "pdf_parse_route": "mineru",
                "pdf_is_scanned": True,
                "mineru_job_id": "task-abc",
            },
        }
    ]
    store = _FakeStore({"锅炉1000问.pdf": rows}, {r["ext_id"]: r for r in rows})
    model = load_citation_preview(
        chunk_id="scan-1",
        namespace="boiler_knowledge",
        doc_name="锅炉1000问.pdf",
        ref_index=1,
        store=store,
    )
    assert model.preview_mode == "pdf"
    assert model.pdf_prefer_text_default is True
    assert model.message and "扫描件" in model.message

    default_html = render_citation_preview_html(
        model,
        request_query="chunk_id=scan-1&namespace=boiler_knowledge",
    )
    assert "pdf.min.js" not in default_html
    assert "本轮引用" in default_html
    assert "PDF 原件预览" in default_html
    assert "view=pdf" in default_html
    assert "扫描件" in default_html

    pdf_html = render_citation_preview_html(
        model,
        view="pdf",
        request_query="chunk_id=scan-1&namespace=boiler_knowledge",
    )
    assert "pdf.min.js" in pdf_html
    assert "citation-preview/pdf-proxy" in pdf_html
    assert "view=text" in pdf_html


def test_locate_failed_pdf_defaults_to_text_preview():
    """PDF 原件存在但分片定位失败时，默认文本预览，避免空跑大文件下载。"""
    rows = [
        {
            "text": "前言",
            "ext_id": "preface",
            "namespace": "ns",
            "doc_name": "手册.pdf",
            "metadata": {
                "chunk_index": 0,
                "source_uri": "https://files.example.com/manual.pdf",
                "content_fetch_content_type": "application/pdf",
            },
        }
    ]
    store = _FakeStore({"手册.pdf": rows}, {})
    model = load_citation_preview(
        chunk_id="missing-id-zzzz",
        namespace="ns",
        doc_name="手册.pdf",
        store=store,
    )
    assert model.preview_mode == "pdf"
    assert model.pdf_prefer_text_default is True
    assert not any(c.is_target for c in model.chunks)
    html = render_citation_preview_html(
        model,
        request_query="chunk_id=missing-id-zzzz&namespace=ns&doc_name=%E6%89%8B%E5%86%8C.pdf",
    )
    assert "pdf.min.js" not in html
    assert "PDF 原件预览" in html
    assert "view=pdf" in html


def test_browser_openable_minio_ref_uses_proxy_path():
    url = browser_openable_original_url("minio://rag-assets/rag-docs/ns/doc/original.pdf")
    assert url is not None
    assert url.startswith("/chatbot/documents/original/preview?ref=")
    assert "minio" in url
    assert browser_openable_original_url("https://cdn.example.com/a.pdf") == "https://cdn.example.com/a.pdf"
    assert browser_openable_original_url("") is None


def test_ensure_citation_preview_urls_backfills_missing():
    out = ensure_citation_preview_urls(
        [
            {
                "ref_index": 1,
                "chunk_id": "c1",
                "namespace": "ns",
                "doc_name": "手册",
                "text_preview": "片段正文",
                "original_content_url": "minio://rag-assets/rag-docs/a.pdf",
            }
        ]
    )
    assert out[0]["preview_url"].startswith("/chatbot/citation-preview?")
    assert out[0]["original_content_url"].startswith("minio://")
    assert "chunk_id=c1" in out[0]["preview_url"]


def test_minio_pdf_preview_passes_ref_to_viewer():
    rows = [
        {
            "text": "地降监测规范条文内容足够长用于搜索",
            "ext_id": "m1",
            "namespace": "djs_kb",
            "doc_name": "规范.pdf",
            "metadata": {
                "chunk_index": 0,
                "source_uri": "minio://rag-assets/rag-docs/djs/规范/original.pdf",
                "content_fetch_content_type": "application/pdf",
            },
        }
    ]
    store = _FakeStore({"规范.pdf": rows}, {r["ext_id"]: r for r in rows})
    model = load_citation_preview(
        chunk_id="m1",
        namespace="djs_kb",
        doc_name="规范.pdf",
        store=store,
    )
    assert model.preview_mode == "pdf"
    html = render_citation_preview_html(
        model,
        request_query="chunk_id=m1&namespace=djs_kb&api_key=secret",
    )
    assert "pdf.min.js" in html
    assert "minio://rag-assets" in html
    assert "isStableObjectRef" in html or "pdf-proxy" in html
    assert "documents/original/preview" in html
    assert "api_key=secret" in html
