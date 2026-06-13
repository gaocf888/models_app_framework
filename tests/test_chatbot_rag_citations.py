"""chatbot_rag_citations 单元测试。"""

from app.llm.graphs.chatbot_rag_citations import (
    chunks_to_numbered_llm_snippets,
    chunks_to_rag_citations,
    chunks_to_rag_context,
)
from app.llm.graphs.chatbot_retrieval_query import format_rag_snippets_system_block
from app.rag.models import RetrievedChunk


def test_chunks_to_rag_citations_shape():
    chunks = [
        RetrievedChunk(
            text="hello world " * 20,
            doc_name="规程A.pdf",
            namespace="锅炉",
            doc_version="v2",
            chunk_id="c1",
            section_path="3.2",
            score=0.91,
        ),
        RetrievedChunk(
            text="hello world " * 20,
            doc_name="规程A.pdf",
            namespace="锅炉",
            chunk_id="c1",
            score=0.5,
        ),
    ]
    cites = chunks_to_rag_citations(chunks)
    assert len(cites) == 1
    assert cites[0]["ref_index"] == 1
    assert cites[0]["doc_name"] == "规程A.pdf"
    assert cites[0]["namespace"] == "锅炉"
    assert cites[0]["doc_version"] == "v2"
    assert cites[0]["chunk_id"] == "c1"
    assert cites[0]["section_path"] == "3.2"
    assert cites[0]["source"] == "vector_store"
    assert "text_preview" in cites[0]
    assert cites[0]["score"] == 0.91


def test_chunks_to_rag_citations_includes_rerank_score():
    chunks = [
        RetrievedChunk(
            text="FAQ 答案",
            doc_name="1000问",
            score=1.98,
            rerank_score=0.999,
        )
    ]
    cites = chunks_to_rag_citations(chunks)
    assert cites[0]["score"] == 1.98
    assert cites[0]["rerank_score"] == 0.999


def test_chunks_to_rag_citations_original_content_url_from_metadata():
    chunks = [
        RetrievedChunk(
            text="正文片段",
            doc_name="规程",
            namespace="ns",
            chunk_id="x1",
            metadata={"source_uri": "https://cdn.example.com/doc.pdf"},
        )
    ]
    cites = chunks_to_rag_citations(chunks)
    assert cites[0].get("original_content_url") == "https://cdn.example.com/doc.pdf"


def test_chunks_to_rag_citations_excludes_nl2sql_namespaces():
    chunks = [
        RetrievedChunk(text="schema ddl", namespace="nl2sql_schema", doc_name="t1", chunk_id="s1"),
        RetrievedChunk(text="biz rule", namespace="nl2sql_biz_knowledge", doc_name="b1", chunk_id="b1"),
        RetrievedChunk(text="qa ex", namespace="nl2sql_qa_examples", doc_name="q1", chunk_id="q1"),
        RetrievedChunk(text="锅炉知识", namespace="boiler_knowledge", doc_name="手册", chunk_id="k1"),
    ]
    cites = chunks_to_rag_citations(chunks)
    assert len(cites) == 1
    assert cites[0]["namespace"] == "boiler_knowledge"


def test_chunks_to_rag_citations_empty():
    assert chunks_to_rag_citations(None) == []
    assert chunks_to_rag_citations([]) == []


def test_chunks_to_rag_context_ref_index_alignment():
    chunks = [
        RetrievedChunk(text="第一段知识", doc_name="手册A", namespace="boiler", chunk_id="a1", section_path="1.1"),
        RetrievedChunk(text="第二段知识", doc_name="手册B", namespace="boiler", chunk_id="b1"),
    ]
    snippets, cites = chunks_to_rag_context(chunks)
    assert len(snippets) == 2
    assert len(cites) == 2
    assert snippets[0].startswith("[1] 《手册A》 1.1\n第一段知识")
    assert snippets[1].startswith("[2] 《手册B》\n第二段知识")
    assert cites[0]["ref_index"] == 1
    assert cites[1]["ref_index"] == 2


def test_chunks_to_numbered_llm_snippets_matches_context():
    chunks = [
        RetrievedChunk(text="内容", doc_name="规程", namespace="ns", chunk_id="x1"),
    ]
    snippets, cites = chunks_to_rag_context(chunks)
    assert chunks_to_numbered_llm_snippets(chunks) == snippets
    assert cites[0]["ref_index"] == 1


def test_format_rag_block_numbered_citation_rule():
    block = format_rag_snippets_system_block(
        ["[1] 《规程》\n条文内容", "[2] 《标准》 2.3\n另一段"]
    )
    assert "[n]" in block or "[n]" in block.replace("``", "")
    assert "禁止编造" in block
    assert "《规程》" in block
