"""地降 PostgreSQL 联调烟测（需真实库；默认跳过）。

启用方式（PowerShell 示例）::

    $env:NL2SQL_PG_SMOKE = "1"
    $env:NL2SQL_BUSINESS_DOMAIN = "subsidence"
    $env:DB_PASSWORD = "<postgres_password>"
    pytest tests/test_nl2sql_subsidence_pg_smoke.py -v

可选覆盖连接（未设则走 profile.db.* 默认）::

    $env:DB_HOST = "192.169.237.197"
    $env:DB_PORT = "5432"
"""

from __future__ import annotations

import os

import pytest

from app.core.config import get_app_config
from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache

_EXPECTED_TABLES = {
    "t_data_wash_fcb",
    "t_data_wash_jyb",
    "t_data_wash_gnss",
    "t_data_wash_dxswj",
    "t_data_wash_kxsylj",
    "t_data_wash_gq",
    "t_data_wash_qxz",
    "t_station",
}

pytestmark = pytest.mark.skipif(
    os.getenv("NL2SQL_PG_SMOKE", "").strip().lower() not in ("1", "true", "yes"),
    reason="Set NL2SQL_PG_SMOKE=1 and DB_PASSWORD to run PG integration smoke",
)


@pytest.fixture(autouse=True)
def _subsidence_pg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    if not (os.getenv("DB_PASSWORD") or os.getenv("DB_URL")):
        pytest.skip("DB_PASSWORD or DB_URL required for PG smoke")
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    yield
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()


@pytest.mark.asyncio
async def test_pg_config_uses_asyncpg_driver() -> None:
    cfg = get_app_config()
    assert cfg.db.dialect == "postgres"
    assert "asyncpg" in cfg.db.url
    assert "charset=" not in cfg.db.url


@pytest.mark.asyncio
async def test_pg_reflect_eight_tables() -> None:
    from app.nl2sql.schema_service import SchemaMetadataService

    svc = SchemaMetadataService()
    await svc.refresh_schema()
    names = {t.name.lower() for t in svc.list_tables() if t.name}
    missing = _EXPECTED_TABLES - names
    assert not missing, f"missing tables in reflection: {sorted(missing)}"


@pytest.mark.asyncio
async def test_pg_simple_select() -> None:
    from app.nl2sql.executor import SQLExecutor

    ex = SQLExecutor()
    rows = await ex.execute("SELECT 1 AS ok")
    assert rows
    assert rows[0].get("ok") == 1


@pytest.mark.asyncio
async def test_pg_fcb_limit_one() -> None:
    from app.nl2sql.executor import SQLExecutor

    ex = SQLExecutor()
    rows = await ex.execute(
        "SELECT station_id, data_time, total_settle "
        "FROM t_data_wash_fcb "
        "ORDER BY data_time DESC NULLS LAST "
        "LIMIT 1"
    )
    assert isinstance(rows, list)
