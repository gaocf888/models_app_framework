"""地降 PostgreSQL schema 路由单元测试（不连真实库）。"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_routing_caches(monkeypatch: pytest.MonkeyPatch):
    from app.nl2sql import schema_routing as sr
    from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache

    clear_nl2sql_business_profile_cache()
    sr.clear_schema_routing_cache()
    yield
    clear_nl2sql_business_profile_cache()
    sr.clear_schema_routing_cache()


def test_default_settlement_schema_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.delenv("SUBSIDENCE_DATA_SOURCE", raising=False)
    monkeypatch.delenv("SUBSIDENCE_SCHEMA_REALTIME", raising=False)
    monkeypatch.delenv("SUBSIDENCE_SCHEMA_CALIBRATED", raising=False)

    from app.nl2sql.schema_routing import (
        get_om_result_schema,
        get_postgres_search_path,
        get_settlement_data_source,
        get_settlement_schema,
        qualify_settlement_table,
    )

    assert get_settlement_data_source() == "realtime"
    assert get_settlement_schema() == "public"
    assert get_om_result_schema() == "om_result"
    assert get_postgres_search_path() == "public"
    assert get_postgres_search_path(include_om_result=True) == "public, om_result"
    assert qualify_settlement_table("t_station") == "public.t_station"


def test_calibrated_requires_schema_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("SUBSIDENCE_DATA_SOURCE", "calibrated")
    monkeypatch.setenv("SUBSIDENCE_SCHEMA_CALIBRATED", "")

    from app.nl2sql.schema_routing import clear_schema_routing_cache, get_settlement_schema

    clear_schema_routing_cache()
    with pytest.raises(ValueError, match="calibrated"):
        get_settlement_schema()


def test_calibrated_schema_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("SUBSIDENCE_DATA_SOURCE", "calibrated")
    monkeypatch.setenv("SUBSIDENCE_SCHEMA_CALIBRATED", "settlement_checked")

    from app.nl2sql.schema_routing import (
        clear_schema_routing_cache,
        get_postgres_search_path,
        get_settlement_schema,
        qualify_om_result_table,
    )

    clear_schema_routing_cache()
    assert get_settlement_schema() == "settlement_checked"
    assert get_postgres_search_path() == "settlement_checked"
    assert get_postgres_search_path(include_om_result=True) == "settlement_checked, om_result"
    assert qualify_om_result_table("t_om_device_alarm_list") == "om_result.t_om_device_alarm_list"


def test_data_source_aliases_default_to_realtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("SUBSIDENCE_DATA_SOURCE", "default")

    from app.nl2sql.schema_routing import clear_schema_routing_cache, get_settlement_data_source

    clear_schema_routing_cache()
    assert get_settlement_data_source() == "realtime"


def test_validator_rejects_unknown_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("NL2SQL_SQL_DIALECT", "postgres")
    monkeypatch.setenv("SUBSIDENCE_SCHEMA_REALTIME", "public")

    from app.core.config import get_app_config
    from app.nl2sql.schema_routing import clear_schema_routing_cache
    from app.nl2sql.validator import SQLValidator

    get_app_config.cache_clear()
    clear_schema_routing_cache()

    v = SQLValidator()
    ok, err = v.validate_identifiers(
        "SELECT 1 FROM evil.t_station",
        allowed_tables={"t_station"},
    )
    assert ok is False
    assert err and "unknown schemas" in err

    ok2, err2 = v.validate_identifiers(
        "SELECT 1 FROM public.t_station",
        allowed_tables={"t_station"},
    )
    assert ok2 is True
    assert err2 is None


def test_validator_rejects_schema_table_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("NL2SQL_SQL_DIALECT", "postgres")
    monkeypatch.setenv("SUBSIDENCE_SCHEMA_REALTIME", "public")

    from app.core.config import get_app_config
    from app.nl2sql.schema_routing import clear_schema_routing_cache
    from app.nl2sql.validator import SQLValidator

    get_app_config.cache_clear()
    clear_schema_routing_cache()

    v = SQLValidator()
    ok, err = v.validate_identifiers(
        "SELECT 1 FROM om_result.t_station",
        allowed_tables={"t_station"},
    )
    assert ok is False
    assert err and "om_result schema cannot reference settlement table" in err

    ok2, err2 = v.validate_identifiers(
        "SELECT 1 FROM public.t_om_device_alarm_list",
        allowed_tables={"t_om_device_alarm_list"},
    )
    assert ok2 is False
    assert err2 and "cannot reference om_result table" in err2


def test_settlement_data_source_label_zh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.delenv("SUBSIDENCE_DATA_SOURCE", raising=False)

    from app.nl2sql.schema_routing import (
        clear_schema_routing_cache,
        settlement_data_source_label_zh,
        settlement_data_source_prompt_hint,
    )

    clear_schema_routing_cache()
    assert settlement_data_source_label_zh() == "准实时沉降数据"
    assert "准实时" in settlement_data_source_prompt_hint()

    monkeypatch.setenv("SUBSIDENCE_DATA_SOURCE", "calibrated")
    monkeypatch.setenv("SUBSIDENCE_SCHEMA_CALIBRATED", "settlement_checked")
    clear_schema_routing_cache()
    assert settlement_data_source_label_zh() == "人工校核沉降数据"


@pytest.mark.asyncio
async def test_executor_sets_search_path_when_subsidence_pg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.setenv("SUBSIDENCE_SCHEMA_REALTIME", "public")

    from app.nl2sql.executor import SQLExecutor
    import app.nl2sql.schema_routing as sr
    from app.nl2sql.schema_routing import clear_schema_routing_cache

    clear_schema_routing_cache()
    monkeypatch.setattr(sr, "should_apply_subsidence_search_path", lambda: True)
    monkeypatch.setattr(
        sr, "get_postgres_search_path", lambda include_om_result=False: "public"
    )

    executed: list[str] = []

    class _Conn:
        async def execute(self, stmt, *args, **kwargs):
            executed.append(str(getattr(stmt, "text", stmt)))

            class _R:
                def keys(self):
                    return []

                def fetchall(self):
                    return []

            return _R()

    await SQLExecutor._maybe_set_search_path(_Conn())
    assert any("SET LOCAL search_path TO public" in s for s in executed)
    assert not any("om_result" in s for s in executed)


def test_om_result_allowlist_has_eight_tables() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    path = root / "configs/nl2sql_business/subsidence/table_scope_om_result.txt"
    assert path.is_file()
    active = [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert len(active) == 8
    assert "t_om_data_alarm_summary" in active
