import os
import unittest

from app.nl2sql.qa_feedback import (
    NL2SQL_QA_AUTO_KIND,
    META_KEY_DATA_SOURCE_FP,
    META_KEY_SCHEMA_FP,
    NL2SQLQARetrievalContext,
    compact_nl2sql_feedback_question,
    format_nl2sql_qa_embedding_text,
    qa_chunk_passes_retrieval_filter,
)
from app.nl2sql.rag_service import NL2SQLRAGService
from app.rag.models import RetrievedChunk


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
