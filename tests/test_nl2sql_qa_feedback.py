import os
import unittest

from app.nl2sql.qa_feedback import (
    NL2SQL_QA_AUTO_KIND,
    META_KEY_DATA_SOURCE_FP,
    META_KEY_SCHEMA_FP,
    NL2SQLQARetrievalContext,
    qa_chunk_passes_retrieval_filter,
)
from app.nl2sql.rag_service import NL2SQLRAGService
from app.rag.models import RetrievedChunk


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
