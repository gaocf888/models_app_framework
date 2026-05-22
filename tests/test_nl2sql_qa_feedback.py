import os
import unittest

from app.nl2sql.qa_feedback import (
    NL2SQL_QA_AUTO_KIND,
    META_KEY_DATA_SOURCE_FP,
    META_KEY_PLAN_TEMPLATE_VERSION,
    META_KEY_SCHEMA_FP,
    NL2SQLQARetrievalContext,
    analysis_accepts_auto_qa_feedback,
    build_nl2sql_auto_qa_doc_name,
    compact_nl2sql_feedback_question,
    format_nl2sql_qa_embedding_text,
    list_nl2sql_auto_qa_entries,
    qa_chunk_passes_retrieval_filter,
    update_nl2sql_auto_qa_entry,
    upsert_nl2sql_auto_qa_pair,
)
from app.nl2sql.rag_service import NL2SQLRAGService
from app.rag.models import RetrievedChunk
from app.rag.rag_service import RAGService
from app.rag.vector_store import InMemoryVectorStore


class _FakeEmbeddingService:
    def embed_text(self, query: str) -> list[float]:
        return [0.1, 0.2]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


class _InMemoryStoreProvider:
    def __init__(self, store: InMemoryVectorStore) -> None:
        self._store = store

    def get_default_store(self) -> InMemoryVectorStore:
        return self._store


def _rag_with_inmemory_store() -> tuple[RAGService, InMemoryVectorStore]:
    store = InMemoryVectorStore()
    rag = RAGService(
        embedding_service=_FakeEmbeddingService(),
        store_provider=_InMemoryStoreProvider(store),
    )
    return rag, store


class TestNl2sqlQaFeedbackCompact(unittest.TestCase):
    def test_compact_strips_plan_context_guide(self) -> None:
        head = "请帮我分析昨天的超温情况。查询超温事件明细。若用户未指定机组/区域，则不要臆造。"
        tail = "请结合以下规则线索：据情况截图\n\n[DOCX_TABLE rows=14]\nx|y"
        self.assertEqual(compact_nl2sql_feedback_question(head + tail), head)

    def test_compact_strips_scope_guard_after_guide(self) -> None:
        """与 analysis_graph_runner._compose_plan_task_question 附加的守卫一致，入库时应去掉。"""
        core = "请帮我分析昨天的超温情况。查询超温事件明细"
        guard = "若用户未指定机组/区域，则不要在 WHERE 中臆造具体锅炉名或墙别。"
        tail = "请结合以下规则线索：DOCX"
        # 等价于 _compose_plan_task_question(user,sq) + _apply_plan_context_guide 前半段
        full = f"{core}。{guard}{tail}"
        self.assertEqual(
            compact_nl2sql_feedback_question(full),
            "请帮我分析昨天的超温情况。查询超温事件明细。",
        )

    def test_format_embedding_minimal_no_prefix_by_default(self) -> None:
        q = "查询超温明细。请结合以下规则线索：巨大表格……"
        sql = "SELECT 1"
        huge_prefix = "你是助手\n" + ("- account_boiler: a,b\n" * 500)
        body = format_nl2sql_qa_embedding_text(
            question=q,
            sql=sql,
            prompt_prefix_snapshot=huge_prefix,
            max_prefix_chars=0,
        )
        self.assertIn("查询超温明细", body)
        self.assertIn(sql, body)
        self.assertNotIn("account_boiler", body)
        self.assertNotIn("预制提示前缀摘要", body)


class TestNl2sqlQaFeedbackDedup(unittest.TestCase):
    def test_doc_name_stable_for_five_tuple(self) -> None:
        a = build_nl2sql_auto_qa_doc_name(
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v1",
        )
        b = build_nl2sql_auto_qa_doc_name(
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v1",
        )
        c = build_nl2sql_auto_qa_doc_name(
            analysis_type="overheat_guidance",
            plan_item_id="q2",
            plan_template_version="v1",
        )
        d = build_nl2sql_auto_qa_doc_name(
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v2",
        )
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, d)

    def test_analysis_accepts_only_with_type_and_plan_item(self) -> None:
        self.assertTrue(analysis_accepts_auto_qa_feedback("overheat_guidance", "q1"))
        self.assertFalse(analysis_accepts_auto_qa_feedback(None, "q1"))
        self.assertFalse(analysis_accepts_auto_qa_feedback("overheat_guidance", None))
        self.assertFalse(analysis_accepts_auto_qa_feedback("", "q1"))

    def test_upsert_skips_second_write_same_slot(self) -> None:
        rag, store = _rag_with_inmemory_store()
        kw = dict(
            question="请分析昨天超温。查询明细",
            sql="SELECT 1",
            data_source_fp="ds1",
            schema_fp="sc1",
            policy_fp="pol1",
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v1",
            prompt_prefix_snapshot=None,
        )
        first = upsert_nl2sql_auto_qa_pair(rag, **kw)
        self.assertIsNotNone(first)
        kw["question"] = "请分析今天超温。查询明细"
        kw["sql"] = "SELECT 2"
        second = upsert_nl2sql_auto_qa_pair(rag, **kw)
        self.assertIsNone(second)
        entries = [
            it
            for it in store._items  # noqa: SLF001
            if it.get("namespace") == NL2SQLRAGService.NS_QA
        ]
        self.assertEqual(1, len(entries))
        self.assertIn("SELECT 1", entries[0].get("text") or "")

    def test_upsert_v1_and_v2_plan_versions_both_stored(self) -> None:
        rag, store = _rag_with_inmemory_store()
        base = dict(
            question="请分析超温",
            sql="SELECT 1",
            data_source_fp="ds1",
            schema_fp="sc1",
            policy_fp="pol1",
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            prompt_prefix_snapshot=None,
        )
        first = upsert_nl2sql_auto_qa_pair(rag, **{**base, "plan_template_version": "v1"})
        second = upsert_nl2sql_auto_qa_pair(rag, **{**base, "plan_template_version": "v2", "sql": "SELECT 2"})
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        entries = [
            it
            for it in store._items  # noqa: SLF001
            if it.get("namespace") == NL2SQLRAGService.NS_QA
        ]
        self.assertEqual(2, len(entries))

    def test_list_filter_by_plan_template_version(self) -> None:
        rag, _store = _rag_with_inmemory_store()
        base = dict(
            question="q",
            sql="SELECT 1",
            data_source_fp="ds1",
            schema_fp="sc1",
            policy_fp="pol1",
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            prompt_prefix_snapshot=None,
        )
        upsert_nl2sql_auto_qa_pair(rag, **{**base, "plan_template_version": "v1"})
        upsert_nl2sql_auto_qa_pair(rag, **{**base, "plan_template_version": "v2", "sql": "SELECT 2"})
        v1_rows = list_nl2sql_auto_qa_entries(
            rag, limit=50, analysis_type="overheat_guidance", plan_template_version="v1"
        )
        self.assertEqual(1, len(v1_rows))
        self.assertEqual("v1", (v1_rows[0].get("metadata") or {}).get(META_KEY_PLAN_TEMPLATE_VERSION))

    def test_patch_recomputes_dedup_key(self) -> None:
        rag, store = _rag_with_inmemory_store()
        doc = upsert_nl2sql_auto_qa_pair(
            rag,
            question="q",
            sql="SELECT 1",
            data_source_fp="ds1",
            schema_fp="sc1",
            policy_fp="pol1",
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v1",
            prompt_prefix_snapshot=None,
        )
        self.assertIsNotNone(doc)
        update_nl2sql_auto_qa_entry(
            rag,
            doc_name=doc,
            question="q2",
            sql="SELECT 9",
            metadata_patch={"question_normalized": "norm"},
        )
        items = store._items.values() if hasattr(store._items, "values") else store._items  # noqa: SLF001
        entry = next(it for it in items if it.get("doc_name") == doc)
        meta = entry.get("metadata") or {}
        self.assertIn("v1", meta.get("dedup_key", ""))
        self.assertEqual("norm", meta.get("question_normalized"))

    def test_upsert_rejects_direct_nl2sql_without_plan_item(self) -> None:
        rag, store = _rag_with_inmemory_store()
        out = upsert_nl2sql_auto_qa_pair(
            rag,
            question="查超温",
            sql="SELECT 1",
            data_source_fp="ds1",
            schema_fp="sc1",
            policy_fp="pol1",
            analysis_type="overheat_guidance",
            plan_item_id=None,
            plan_template_version=None,
            prompt_prefix_snapshot=None,
        )
        self.assertIsNone(out)
        self.assertEqual(
            0,
            sum(1 for it in store._items if it.get("namespace") == NL2SQLRAGService.NS_QA),  # noqa: SLF001
        )


class TestNl2sqlQaFeedbackFilter(unittest.TestCase):
    def test_auto_chunk_requires_matching_fps(self) -> None:
        ctx = NL2SQLQARetrievalContext(
            data_source_fp="ds1",
            schema_fp="sc1",
            policy_fp="pol1",
            analysis_type="overheat",
        )
        chunk = RetrievedChunk(
            text="x",
            namespace=NL2SQLRAGService.NS_QA,
            metadata={
                "nl2sql_auto_kind": NL2SQL_QA_AUTO_KIND,
                "ingest_source": "auto",
                META_KEY_DATA_SOURCE_FP: "ds1",
                META_KEY_SCHEMA_FP: "sc1",
                "policy_fp": "pol1",
                "analysis_type": "overheat",
            },
        )
        self.assertTrue(qa_chunk_passes_retrieval_filter(chunk, ctx))

    def test_auto_chunk_rejected_on_schema_mismatch(self) -> None:
        ctx = NL2SQLQARetrievalContext(
            data_source_fp="ds1",
            schema_fp="sc_new",
            policy_fp="pol1",
            analysis_type=None,
        )
        chunk = RetrievedChunk(
            text="x",
            namespace=NL2SQLRAGService.NS_QA,
            metadata={
                "nl2sql_auto_kind": NL2SQL_QA_AUTO_KIND,
                "ingest_source": "auto",
                META_KEY_DATA_SOURCE_FP: "ds1",
                META_KEY_SCHEMA_FP: "sc_old",
                "policy_fp": "pol1",
            },
        )
        self.assertFalse(qa_chunk_passes_retrieval_filter(chunk, ctx))

    def test_schema_namespace_always_passes_with_ctx(self) -> None:
        ctx = NL2SQLQARetrievalContext(data_source_fp="ds1", schema_fp="sc1", policy_fp="p")
        chunk = RetrievedChunk(text="schema doc", namespace=NL2SQLRAGService.NS_SCHEMA)
        self.assertTrue(qa_chunk_passes_retrieval_filter(chunk, ctx))

    def test_legacy_unscoped_respects_env(self) -> None:
        prev = os.environ.get("NL2SQL_QA_INCLUDE_LEGACY_UNSCOPED")
        try:
            os.environ["NL2SQL_QA_INCLUDE_LEGACY_UNSCOPED"] = "false"
            ctx = NL2SQLQARetrievalContext(data_source_fp="ds1", schema_fp="sc1", policy_fp="p")
            chunk = RetrievedChunk(
                text="old manual qa",
                namespace=NL2SQLRAGService.NS_QA,
                metadata={"doc_version": "v1"},
            )
            self.assertFalse(qa_chunk_passes_retrieval_filter(chunk, ctx))
        finally:
            if prev is None:
                os.environ.pop("NL2SQL_QA_INCLUDE_LEGACY_UNSCOPED", None)
            else:
                os.environ["NL2SQL_QA_INCLUDE_LEGACY_UNSCOPED"] = prev


if __name__ == "__main__":
    unittest.main()
