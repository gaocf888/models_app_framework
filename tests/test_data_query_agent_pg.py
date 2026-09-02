"""数据查询智能体：地降 PostgreSQL 集成（默认跳过）。

不调用 LLM；用 schema 反射 + align_semantics/link_schema/_resolve_table_scope + 真实 JOIN SQL。

启用方式（PowerShell）::

    $env:NL2SQL_PG_SMOKE = "1"
    $env:NL2SQL_BUSINESS_DOMAIN = "subsidence"
    $env:DB_PASSWORD = "<postgres_password>"
    pytest tests/test_data_query_agent_pg.py -v
"""

from __future__ import annotations

import os

import pytest

from app.core.config import get_app_config
from app.data_query_agent.catalog import get_library_catalog
from app.data_query_agent.library_intent import resolve_library_intent
from app.data_query_agent.scope_intent import resolve_scope_intent
from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache
from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
from app.nl2sql.schema_linker import link_schema
from app.nl2sql.semantic_layer import align_semantics, clear_semantic_assets_cache, load_semantic_assets

pytestmark = pytest.mark.skipif(
    os.getenv("NL2SQL_PG_SMOKE", "").strip().lower() not in ("1", "true", "yes"),
    reason="Set NL2SQL_PG_SMOKE=1 and DB_PASSWORD to run PG integration",
)


@pytest.fixture(autouse=True)
def _subsidence_pg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    if not (os.getenv("DB_PASSWORD") or os.getenv("DB_URL")):
        pytest.skip("DB_PASSWORD or DB_URL required for PG integration")
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()
    yield
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()


def _assets():
    from pathlib import Path

    from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile

    profile = get_nl2sql_business_profile()
    assert profile is not None
    return load_semantic_assets(
        str((Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    )


def _table_columns(tables) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for t in tables:
        name = str(t.name or "").strip().lower()
        if not name:
            continue
        out[name] = {str(c.name).strip().lower() for c in (t.columns or []) if c.name}
    return out


@pytest.mark.asyncio
async def test_pg_path1_fcb_lock_and_join() -> None:
    from app.nl2sql.executor import SQLExecutor
    from app.nl2sql.schema_service import SchemaMetadataService

    cat = get_library_catalog()
    query = "朝阳区年沉降比较大的监测点"
    intent = resolve_library_intent(query, "fcb", catalog=cat)
    assert intent.ok and intent.library is not None
    assert intent.source == "request"
    assert intent.library.table == "t_data_wash_fcb"
    scope = resolve_scope_intent(query, intent.library)
    assert scope.confirmed_scope.get("district") == "朝阳区"
    assert scope.confirmed_scope.get("device_type") == "fcb"

    svc = SchemaMetadataService()
    await svc.refresh_schema()
    table_columns = _table_columns(svc.list_tables())
    assert "t_data_wash_fcb" in table_columns
    assert "t_station" in table_columns

    chain = object.__new__(NL2SQLChain)
    scoped = chain._resolve_table_scope(
        analysis_type="data_query",
        table_columns=table_columns,
        forced_tables=["t_data_wash_fcb"],
    )
    assert "t_data_wash_fcb" in scoped
    assert "t_station" in scoped
    assert "t_data_wash_jyb" not in scoped

    assets = _assets()
    q_intent = QuestionIntent(
        raw_question=query,
        scope_question=query,
        time_window=None,
        scope=QuestionScopeIntent(device_type="fcb", district="朝阳区"),
    )
    binding = align_semantics(query, q_intent, assets=assets, forced_tables=["t_data_wash_fcb"])
    linked = link_schema(
        query,
        q_intent,
        binding,
        table_columns,
        allowlist=set(table_columns.keys()),
        assets=assets,
        forced_tables=["t_data_wash_fcb"],
    )
    names = {t.name.lower() for t in linked.tables}
    assert "t_data_wash_fcb" in names
    assert "t_data_wash_jyb" not in names

    rows = await SQLExecutor().execute(
        "SELECT f.station_id, s.area, f.total_settle, f.data_time "
        "FROM t_data_wash_fcb f "
        "INNER JOIN t_station s ON f.project_name = s.name "
        "WHERE s.area = '朝阳区' "
        "ORDER BY f.data_time DESC NULLS LAST "
        "LIMIT 5"
    )
    assert isinstance(rows, list)


@pytest.mark.asyncio
async def test_pg_jyb_settle_locks_jyb_not_fcb() -> None:
    from app.nl2sql.executor import SQLExecutor
    from app.nl2sql.schema_service import SchemaMetadataService

    cat = get_library_catalog()
    query = "朝阳区沉降大的点"
    intent = resolve_library_intent(query, "jyb", catalog=cat)
    assert intent.ok and intent.library is not None
    assert intent.library.table == "t_data_wash_jyb"

    svc = SchemaMetadataService()
    await svc.refresh_schema()
    table_columns = _table_columns(svc.list_tables())
    chain = object.__new__(NL2SQLChain)
    scoped = chain._resolve_table_scope(
        analysis_type="data_query",
        table_columns=table_columns,
        forced_tables=["t_data_wash_jyb"],
    )
    assert "t_data_wash_jyb" in scoped
    assert "t_data_wash_fcb" not in scoped

    assets = _assets()
    q_intent = QuestionIntent(
        raw_question=query,
        scope_question=query,
        time_window=None,
        scope=QuestionScopeIntent(device_type="jyb", district="朝阳区"),
    )
    binding = align_semantics(query, q_intent, assets=assets, forced_tables=["t_data_wash_jyb"])
    linked = link_schema(
        query,
        q_intent,
        binding,
        table_columns,
        allowlist=set(table_columns.keys()),
        assets=assets,
        forced_tables=["t_data_wash_jyb"],
    )
    names = {t.name.lower() for t in linked.tables}
    assert "t_data_wash_jyb" in names
    assert "t_data_wash_fcb" not in names

    rows = await SQLExecutor().execute(
        "SELECT j.station_id, s.area, j.total_settle "
        "FROM t_data_wash_jyb j "
        "INNER JOIN t_station s ON j.project_name = s.name "
        "WHERE s.area = '朝阳区' "
        "LIMIT 3"
    )
    assert isinstance(rows, list)
    # 锁表语义：本用例 SQL 主表为 jyb，不得出现 fcb
    sql = (
        "SELECT j.station_id FROM t_data_wash_jyb j "
        "INNER JOIN t_station s ON j.project_name = s.name LIMIT 1"
    )
    assert "t_data_wash_fcb" not in sql.lower()
    await SQLExecutor().execute(sql)


@pytest.mark.asyncio
async def test_pg_path2_layered_stations_daxing() -> None:
    from app.nl2sql.executor import SQLExecutor
    from app.nl2sql.schema_service import SchemaMetadataService

    cat = get_library_catalog()
    query = "大兴区有哪些分层监测点？"
    intent = resolve_library_intent(query, None, catalog=cat)
    assert intent.ok and intent.library is not None
    assert intent.library.id == "fcb"
    assert intent.source == "parsed"
    scope = resolve_scope_intent(query, intent.library)
    assert scope.confirmed_scope.get("district") == "大兴区"
    assert scope.confirmed_scope.get("device_type") == "fcb"

    svc = SchemaMetadataService()
    await svc.refresh_schema()
    table_columns = _table_columns(svc.list_tables())
    chain = object.__new__(NL2SQLChain)
    scoped = chain._resolve_table_scope(
        analysis_type="data_query",
        table_columns=table_columns,
        forced_tables=["t_data_wash_fcb"],
    )
    assert scoped & {"t_data_wash_fcb"}
    assert "t_data_wash_gnss" not in scoped

    rows = await SQLExecutor().execute(
        "SELECT f.station_id, s.area "
        "FROM t_data_wash_fcb f "
        "INNER JOIN t_station s ON f.project_name = s.name "
        "WHERE s.area = '大兴区' "
        "LIMIT 5"
    )
    assert isinstance(rows, list)
