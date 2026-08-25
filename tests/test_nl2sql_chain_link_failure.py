"""Schema 链接 refuse / best_effort 链级行为（mock LLM，不连库）。"""

from __future__ import annotations

from unittest import mock

import pytest

from app.core.config import get_app_config
from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache
from app.nl2sql.schema_linker import LinkedSchema
from app.nl2sql.schema_service import TableColumn, TableSchema
from app.nl2sql.semantic_layer import clear_semantic_assets_cache
from app.nl2sql.validator import SQLValidator

_SUBSIDENCE_TABLES = (
    "t_data_wash_fcb",
    "t_data_wash_jyb",
    "t_data_wash_gnss",
    "t_data_wash_dxswj",
    "t_data_wash_kxsylj",
    "t_data_wash_gq",
    "t_data_wash_qxz",
    "t_station",
)


def _table_schemas() -> list[TableSchema]:
    cols = [
        TableColumn("data_time", "timestamp"),
        TableColumn("total_settle", "numeric"),
        TableColumn("station_id", "varchar"),
        TableColumn("project_name", "varchar"),
    ]
    station_cols = [
        TableColumn("name", "varchar"),
        TableColumn("area", "varchar"),
    ]
    out: list[TableSchema] = []
    for name in _SUBSIDENCE_TABLES:
        out.append(
            TableSchema(
                name=name,
                columns=station_cols if name == "t_station" else cols,
                foreign_keys=[],
            )
        )
    return out


def _build_chain() -> NL2SQLChain:
    chain = object.__new__(NL2SQLChain)
    chain._validator = SQLValidator()
    chain._tidb_forbidden_aliases = set(NL2SQLChain._tidb_forbidden_aliases_default)
    chain._lc_chat_model = None
    chain._llm = mock.Mock()
    chain._llm.generate = mock.AsyncMock(
        return_value="SELECT total_settle FROM t_data_wash_fcb LIMIT 1"
    )
    tables = _table_schemas()
    chain._schema = mock.Mock()
    chain._schema.list_tables = mock.Mock(return_value=tables)
    chain._rag = mock.Mock()
    chain._rag.retrieve = mock.Mock(return_value=[])
    chain._prompt_builder = mock.Mock()
    chain._prompt_builder.build = mock.Mock(return_value="test prompt")
    chain._db_schema_available = mock.Mock(return_value=True)
    chain._ensure_schema_refreshed_once = mock.AsyncMock()
    allowed = {t.name for t in tables}
    col_map = {
        t.name: {c.name for c in t.columns}
        for t in tables
    }
    chain._whitelist_from_schema_and_snippets = mock.Mock(
        return_value=(allowed, set(), True)
    )
    chain._table_columns_map = mock.Mock(return_value=col_map)
    chain._resolve_table_scope = mock.Mock(return_value=allowed)
    chain._build_join_whitelist = mock.Mock(return_value=frozenset())
    chain._format_enriched_schema_catalog = mock.Mock(return_value="catalog")
    chain._build_schema_catalog_hint = mock.Mock(return_value="hint")
    chain._ls_tracker = mock.Mock()
    tpl = mock.Mock()
    tpl.content = "SQL generator {{NL2SQL_SCHEMA_CATALOG}}"
    chain._prompts = mock.Mock()
    chain._prompts.get_template = mock.Mock(return_value=tpl)
    return chain


@pytest.fixture(autouse=True)
def _subsidence_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("NL2SQL_SQL_CACHE_ENABLED", "false")
    monkeypatch.setenv("NL2SQL_L1_CACHE_ENABLED", "false")
    monkeypatch.delenv("NL2SQL_SEMANTIC_LINK_ENABLED", raising=False)
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()
    yield
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()


@pytest.mark.asyncio
async def test_link_refuse_skips_llm() -> None:
    chain = _build_chain()
    failed = LinkedSchema(status="failed", fail_reason="no_metric_table")

    with mock.patch("app.nl2sql.schema_linker.link_schema", return_value=failed):
        sql, ctx = await chain.generate_sql_with_validation_context(
            "朝阳区监测点沉降多少",
            on_link_failure="refuse",
        )

    assert sql == ""
    assert ctx.parsed_intent is not None
    assert "link_failed" in (ctx.parsed_intent.get("gen_fail_reason") or "")
    chain._llm.generate.assert_not_called()


@pytest.mark.asyncio
async def test_link_best_effort_still_calls_llm() -> None:
    chain = _build_chain()
    failed = LinkedSchema(status="failed", fail_reason="weak_match")

    with mock.patch("app.nl2sql.schema_linker.link_schema", return_value=failed):
        sql, ctx = await chain.generate_sql_with_validation_context(
            "朝阳区监测点沉降多少",
            on_link_failure="best_effort",
        )

    chain._llm.generate.assert_called_once()
    assert "link_weak" in (ctx.parsed_intent.get("gen_fail_reason") or "")
    assert (sql or "").strip() != ""


@pytest.mark.asyncio
async def test_nl2sql_service_exposes_gen_fail_reason_on_refuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.models.nl2sql import NL2SQLQueryRequest
    from app.services.nl2sql_service import NL2SQLService

    chain = _build_chain()
    failed = LinkedSchema(status="failed", fail_reason="no_metric_table")
    service = NL2SQLService(chain=chain, executor=mock.Mock(), conv_manager=mock.Mock())

    with mock.patch("app.nl2sql.schema_linker.link_schema", return_value=failed):
        resp = await service.query(
            NL2SQLQueryRequest(
                user_id="u1",
                session_id="s1",
                question="监测点沉降",
                on_link_failure="refuse",
            ),
            record_conversation=False,
            include_parsed_intent=True,
        )

    assert resp.sql == ""
    assert resp.gen_fail_reason is not None
    assert "link_failed" in resp.gen_fail_reason
