import os
import unittest
from unittest import mock

import pytest

from app.llm.langsmith_tracker import LangSmithTracker
from app.nl2sql.chain import NL2SQLChain, NL2SQLValidationContext
from app.nl2sql.qa_feedback import (
    NL2SQLQARetrievalContext,
    create_nl2sql_auto_qa_entry,
)
from app.nl2sql.rag_service import NL2SQLRAGService
from app.nl2sql.schema_service import TableColumn, TableSchema
from app.nl2sql.validator import SQLValidator
from tests.test_nl2sql_qa_feedback import _rag_with_inmemory_store


class _FakeSchema:
    def __init__(self, tables: list[TableSchema]) -> None:
        self._tables = tables

    def list_tables(self) -> list[TableSchema]:
        return self._tables


def _build_chain_for_replay() -> NL2SQLChain:
    chain = object.__new__(NL2SQLChain)
    chain._validator = SQLValidator()
    chain._tidb_forbidden_aliases = set(NL2SQLChain._tidb_forbidden_aliases_default)
    chain._ls_tracker = LangSmithTracker()
    chain._schema = _FakeSchema(
        [
            TableSchema(
                name="monitor_hotarea_temp",
                columns=[
                    TableColumn("id", "BIGINT"),
                    TableColumn("boiler_id", "BIGINT"),
                    TableColumn("start_time", "DATETIME"),
                    TableColumn("pi_code", "VARCHAR"),
                    TableColumn("highest_temp", "DECIMAL"),
                    TableColumn("limit_temp", "DECIMAL"),
                ],
                foreign_keys=[],
            ),
        ]
    )
    return chain


def _validation_ctx() -> NL2SQLValidationContext:
    cols = frozenset({"id", "boiler_id", "start_time", "pi_code", "highest_temp", "limit_temp"})
    return NL2SQLValidationContext(
        allowed_tables=frozenset({"monitor_hotarea_temp"}),
        allowed_columns=cols,
        schema_ok=True,
        table_columns={"monitor_hotarea_temp": cols},
        join_whitelist=frozenset(),
    )


def test_postprocess_qa_replay_skips_column_whitelist() -> None:
    """子查询派生列（如 over_level）不参与 flat 列白名单；qa_replay 与 cache_l2 路径均应通过。"""
    chain = _build_chain_for_replay()
    sql_with_derived = """
    SELECT x.over_level, x.max_delta
    FROM (
      SELECT
        t.pi_code,
        MAX(t.highest_temp - t.limit_temp) AS max_delta,
        '严重超温' AS over_level
      FROM monitor_hotarea_temp t
      GROUP BY t.pi_code
    ) x
    WHERE x.over_level <> '正常'
    """
    ctx = _validation_ctx()
    _, ok_replay, reason_replay = chain._postprocess_and_validate_candidate_sql(
        sql_with_derived,
        question="按严重度汇总测点",
        time_intent_source="按严重度汇总测点",
        validation_ctx=ctx,
        entity_rules=[],
        log_label="qa_replay",
    )
    assert ok_replay, reason_replay

    _, ok_cache, reason_cache = chain._postprocess_and_validate_candidate_sql(
        sql_with_derived,
        question="按严重度汇总测点",
        time_intent_source="按严重度汇总测点",
        validation_ctx=ctx,
        entity_rules=[],
        log_label="cache_l2",
    )
    assert ok_cache, reason_cache


@pytest.mark.asyncio
async def test_try_qa_slot_strict_replay_returns_valid_sql(monkeypatch) -> None:
    monkeypatch.setenv("NL2SQL_QA_SLOT_STRICT_REPLAY", "true")
    rag, _store = _rag_with_inmemory_store()
    fps = {
        "data_source_fp": "ds1",
        "schema_fp": "sc1",
        "policy_fp": "pol1",
    }
    create_nl2sql_auto_qa_entry(
        rag,
        question="q1 question",
        sql="SELECT id FROM monitor_hotarea_temp LIMIT 1",
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        plan_template_version="v2",
        prompt_prefix_snapshot=None,
        mode="replace",
        **fps,
    )
    chain = _build_chain_for_replay()
    chain._rag = NL2SQLRAGService(rag_service=rag)
    ctx = NL2SQLQARetrievalContext(
        **fps,
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        plan_template_version="v2",
    )
    sql = await chain._try_qa_slot_strict_replay(
        nl2sql_qa_ctx=ctx,
        question="请分析超温情况",
        time_intent_source="请分析超温情况",
        validation_ctx=_validation_ctx(),
        entity_rules=[],
        user_id="u1",
        plan_item_id="q1",
    )
    assert sql is not None
    assert "SELECT id FROM monitor_hotarea_temp LIMIT 1" in sql


@pytest.mark.asyncio
async def test_try_qa_slot_strict_replay_none_on_slot_miss(monkeypatch) -> None:
    monkeypatch.setenv("NL2SQL_QA_SLOT_STRICT_REPLAY", "true")
    rag, _store = _rag_with_inmemory_store()
    chain = _build_chain_for_replay()
    chain._rag = NL2SQLRAGService(rag_service=rag)
    ctx = NL2SQLQARetrievalContext(
        data_source_fp="ds1",
        schema_fp="sc1",
        policy_fp="pol1",
        analysis_type="overheat_guidance",
        plan_item_id="q_missing",
        plan_template_version="v2",
    )
    sql = await chain._try_qa_slot_strict_replay(
        nl2sql_qa_ctx=ctx,
        question="请分析超温情况",
        time_intent_source="请分析超温情况",
        validation_ctx=_validation_ctx(),
        entity_rules=[],
        user_id="u1",
        plan_item_id="q_missing",
    )
    assert sql is None


@pytest.mark.asyncio
async def test_try_qa_slot_strict_replay_applies_tidb_and_filter(monkeypatch) -> None:
    monkeypatch.setenv("NL2SQL_QA_SLOT_STRICT_REPLAY", "true")
    rag, _store = _rag_with_inmemory_store()
    fps = {
        "data_source_fp": "ds1",
        "schema_fp": "sc1",
        "policy_fp": "pol1",
    }
    qa_sql = (
        "SELECT id FROM monitor_hotarea_temp "
        "WHERE start_time BETWEEN '2024-01-01 00:00:00' AND '2024-01-07 23:59:59'"
    )
    create_nl2sql_auto_qa_entry(
        rag,
        question="q1 question",
        sql=qa_sql,
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        plan_template_version="v2",
        prompt_prefix_snapshot=None,
        mode="replace",
        **fps,
    )
    chain = _build_chain_for_replay()
    chain._rag = NL2SQLRAGService(rag_service=rag)
    ctx = NL2SQLQARetrievalContext(
        **fps,
        analysis_type="overheat_guidance",
        plan_item_id="q1",
        plan_template_version="v2",
    )
    sql = await chain._try_qa_slot_strict_replay(
        nl2sql_qa_ctx=ctx,
        question="请分析近一周超温原因",
        time_intent_source="请分析近一周超温原因",
        validation_ctx=_validation_ctx(),
        entity_rules=[],
        user_id="u1",
        plan_item_id="q1",
    )
    assert sql is not None
    assert "DATE_SUB(NOW(), INTERVAL 7 DAY)" in sql


class TestNl2sqlChainStrictReplaySwitch(unittest.TestCase):
    def test_strict_replay_disabled_by_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            os.environ.pop("NL2SQL_QA_SLOT_STRICT_REPLAY", None)
            os.environ.pop("NL2SQL_QA_SLOT_STRICT_REPLAY_OVERHEAT_GUIDANCE", None)
            from app.nl2sql.qa_feedback import nl2sql_qa_slot_strict_replay_enabled

            self.assertFalse(nl2sql_qa_slot_strict_replay_enabled("overheat_guidance"))
