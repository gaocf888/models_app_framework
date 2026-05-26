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
    create_nl2sql_auto_qa_entry,
    fetch_nl2sql_qa_chunks_by_slot,
    format_nl2sql_qa_embedding_text,
    list_nl2sql_auto_qa_entries,
    nl2sql_qa_slot_lookup_eligible,
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


class TestNl2sqlAutoQaAdminCreate(unittest.TestCase):
    _base = dict(
        question="请分析超温。查询明细",
        sql="SELECT 1",
        data_source_fp="ds1",
        schema_fp="sc1",
        policy_fp="pol1",
        analysis_type="overheat_guidance",
        plan_item_id="q2a",
        plan_template_version="v2",
        prompt_prefix_snapshot=None,
    )

    def test_create_replace_writes_new_entry(self) -> None:
        rag, store = _rag_with_inmemory_store()
        doc, created, dedup_key = create_nl2sql_auto_qa_entry(rag, **self._base, mode="replace")
        self.assertTrue(created)
        self.assertIn("v2", dedup_key)
        self.assertIsNotNone(doc)
        entries = [
            it for it in store._items if it.get("namespace") == NL2SQLRAGService.NS_QA  # noqa: SLF001
        ]
        self.assertEqual(1, len(entries))
        self.assertIn("SELECT 1", entries[0].get("text") or "")

    def test_create_skip_if_exists_skips_second(self) -> None:
        rag, store = _rag_with_inmemory_store()
        first_doc, first_created, _ = create_nl2sql_auto_qa_entry(rag, **self._base, mode="replace")
        self.assertTrue(first_created)
        second_doc, second_created, _ = create_nl2sql_auto_qa_entry(
            rag,
            **{**self._base, "sql": "SELECT 2"},
            mode="skip_if_exists",
        )
        self.assertEqual(first_doc, second_doc)
        self.assertFalse(second_created)
        entries = [
            it for it in store._items if it.get("namespace") == NL2SQLRAGService.NS_QA  # noqa: SLF001
        ]
        self.assertEqual(1, len(entries))
        self.assertIn("SELECT 1", entries[0].get("text") or "")

    def test_create_replace_overwrites_existing(self) -> None:
        rag, store = _rag_with_inmemory_store()
        doc, _, _ = create_nl2sql_auto_qa_entry(rag, **self._base, mode="replace")
        _, created, _ = create_nl2sql_auto_qa_entry(
            rag,
            **{**self._base, "sql": "SELECT 9"},
            mode="replace",
        )
        self.assertTrue(created)
        items = store._items.values() if hasattr(store._items, "values") else store._items  # noqa: SLF001
        entry = next(it for it in items if it.get("doc_name") == doc)
        self.assertIn("SELECT 9", entry.get("text") or "")

    def test_create_rejects_missing_plan_item(self) -> None:
        rag, _store = _rag_with_inmemory_store()
        with self.assertRaises(ValueError):
            create_nl2sql_auto_qa_entry(
                rag,
                **{**self._base, "plan_item_id": ""},
                mode="replace",
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


class TestNl2sqlQaSlotLookup(unittest.TestCase):
    _fps = dict(data_source_fp="ds1", schema_fp="sc1", policy_fp="pol1")

    def test_slot_eligible_requires_plan_template_version(self) -> None:
        ctx = NL2SQLQARetrievalContext(
            **self._fps,
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version=None,
        )
        self.assertFalse(nl2sql_qa_slot_lookup_eligible(ctx))
        ctx_v2 = NL2SQLQARetrievalContext(
            **self._fps,
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v2",
        )
        self.assertTrue(nl2sql_qa_slot_lookup_eligible(ctx_v2))

    def test_slot_not_eligible_without_plan_item(self) -> None:
        ctx = NL2SQLQARetrievalContext(
            **self._fps,
            analysis_type="overheat_guidance",
            plan_item_id=None,
            plan_template_version="v2",
        )
        self.assertFalse(nl2sql_qa_slot_lookup_eligible(ctx))

    def test_fetch_by_slot_returns_exact_doc(self) -> None:
        rag, _store = _rag_with_inmemory_store()
        create_nl2sql_auto_qa_entry(
            rag,
            question="q1 question",
            sql="SELECT slot_q1",
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v2",
            prompt_prefix_snapshot=None,
            mode="replace",
            **self._fps,
        )
        create_nl2sql_auto_qa_entry(
            rag,
            question="q2a question",
            sql="SELECT slot_q2a",
            analysis_type="overheat_guidance",
            plan_item_id="q2a",
            plan_template_version="v2",
            prompt_prefix_snapshot=None,
            mode="replace",
            **self._fps,
        )
        ctx = NL2SQLQARetrievalContext(
            **self._fps,
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v2",
        )
        chunks = fetch_nl2sql_qa_chunks_by_slot(rag, ctx)
        self.assertEqual(1, len(chunks))
        self.assertIn("SELECT slot_q1", chunks[0].text or "")
        self.assertNotIn("SELECT slot_q2a", chunks[0].text or "")

    def test_fetch_by_slot_uses_doc_name_not_metadata_scan_cap(self) -> None:
        """doc_name 直取不受 list_nl2sql_auto_qa_entries(limit=1) 的 scan_cap=20 限制。"""
        rag, _store = _rag_with_inmemory_store()
        for i in range(25):
            create_nl2sql_auto_qa_entry(
                rag,
                question=f"decoy question {i}",
                sql=f"SELECT decoy_{i}",
                analysis_type="overheat_guidance",
                plan_item_id=f"dec{i}",
                plan_template_version="v2",
                prompt_prefix_snapshot=None,
                mode="replace",
                **self._fps,
            )
        create_nl2sql_auto_qa_entry(
            rag,
            question="q2a question",
            sql="SELECT slot_q2a",
            analysis_type="overheat_guidance",
            plan_item_id="q2a",
            plan_template_version="v2",
            prompt_prefix_snapshot=None,
            mode="replace",
            **self._fps,
        )
        ctx = NL2SQLQARetrievalContext(
            **self._fps,
            analysis_type="overheat_guidance",
            plan_item_id="q2a",
            plan_template_version="v2",
        )
        chunks = fetch_nl2sql_qa_chunks_by_slot(rag, ctx)
        self.assertEqual(1, len(chunks))
        self.assertIn("SELECT slot_q2a", chunks[0].text or "")

    def test_fetch_by_slot_empty_on_fingerprint_mismatch(self) -> None:
        rag, _store = _rag_with_inmemory_store()
        create_nl2sql_auto_qa_entry(
            rag,
            question="q1 question",
            sql="SELECT slot_q1",
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v2",
            prompt_prefix_snapshot=None,
            mode="replace",
            **self._fps,
        )
        ctx = NL2SQLQARetrievalContext(
            data_source_fp="ds1",
            schema_fp="sc_other",
            policy_fp="pol1",
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v2",
        )
        self.assertEqual([], fetch_nl2sql_qa_chunks_by_slot(rag, ctx))

    def test_rag_service_slot_hit_skips_vector_qa(self) -> None:
        rag, _store = _rag_with_inmemory_store()
        rag._rerank = lambda query, hits: hits  # noqa: SLF001
        create_nl2sql_auto_qa_entry(
            rag,
            question="q1 question",
            sql="SELECT slot_q1",
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v2",
            prompt_prefix_snapshot=None,
            mode="replace",
            **self._fps,
        )
        rag.index_texts(
            ["decoy vector qa should not win when slot hits"],
            namespace=NL2SQLRAGService.NS_QA,
            doc_name="manual_decoy",
        )
        svc = NL2SQLRAGService(rag_service=rag)
        ctx = NL2SQLQARetrievalContext(
            **self._fps,
            analysis_type="overheat_guidance",
            plan_item_id="q1",
            plan_template_version="v2",
        )
        chunks = svc.retrieve_chunks("unrelated query text", nl2sql_qa_context=ctx)
        qa_chunks = [c for c in chunks if c.namespace == NL2SQLRAGService.NS_QA]
        self.assertEqual(1, len(qa_chunks))
        self.assertIn("SELECT slot_q1", qa_chunks[0].text or "")

    def test_rag_service_without_plan_item_uses_vector_qa(self) -> None:
        rag, _store = _rag_with_inmemory_store()
        rag._rerank = lambda query, hits: hits  # noqa: SLF001
        rag.index_texts(
            ["vector qa decoy text for retrieval"],
            namespace=NL2SQLRAGService.NS_QA,
            doc_name="manual_only",
        )
        svc = NL2SQLRAGService(rag_service=rag)
        ctx = NL2SQLQARetrievalContext(
            **self._fps,
            analysis_type=None,
            plan_item_id=None,
            plan_template_version=None,
        )
        chunks = svc.retrieve_chunks("vector qa decoy", nl2sql_qa_context=ctx)
        qa_chunks = [c for c in chunks if c.namespace == NL2SQLRAGService.NS_QA]
        self.assertGreaterEqual(len(qa_chunks), 1)
        self.assertIn("vector qa decoy", qa_chunks[0].text or "")


if __name__ == "__main__":
    unittest.main()
