"""Schema 链接单元测试。"""

from __future__ import annotations

import pytest

from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache
from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
from app.nl2sql.schema_linker import link_schema
from app.nl2sql.semantic_layer import align_semantics, clear_semantic_assets_cache, load_semantic_assets


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()
    yield
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()


def test_narrow_validation_sets_uses_bare_column_names() -> None:
    from app.nl2sql.schema_linker import (
        LinkedColumn,
        LinkedSchema,
        LinkedTable,
        narrow_validation_sets,
    )

    linked = LinkedSchema(
        status="ok",
        tables=[
            LinkedTable(name="t_data_wash_fcb", reason="primary", score=1.0),
            LinkedTable(name="t_station", reason="join", score=0.8),
        ],
        columns=[
            LinkedColumn(table="t_data_wash_fcb", column="station_id", role="key", reason="id"),
            LinkedColumn(table="t_data_wash_fcb", column="total_settle", role="measure", reason="metric"),
            LinkedColumn(table="t_data_wash_fcb", column="data_time", role="time", reason="time"),
            LinkedColumn(table="t_data_wash_fcb", column="project_name", role="key", reason="join"),
            LinkedColumn(table="t_station", column="name", role="key", reason="join"),
            LinkedColumn(table="t_station", column="area", role="dim", reason="district"),
        ],
        joins=[],
        confidence=0.9,
    )
    allowed_tables = {"t_data_wash_fcb", "t_station", "t_data_wash_jyb"}
    allowed_columns = {
        "station_id",
        "total_settle",
        "data_time",
        "project_name",
        "name",
        "area",
        "code",
    }
    table_columns = {
        "t_data_wash_fcb": {"station_id", "total_settle", "data_time", "project_name", "station_name"},
        "t_station": {"name", "area", "code", "id"},
        "t_data_wash_jyb": {"station_id", "total_settle"},
    }
    new_tables, new_cols, new_tc = narrow_validation_sets(
        linked,
        allowed_tables,
        allowed_columns,
        table_columns,
        mode="linked_only",
    )
    assert new_tables == {"t_data_wash_fcb", "t_station"}
    assert "t_data_wash_jyb" not in new_tc
    # 裸列名，且不含 table.column
    assert all("." not in c for c in new_cols)
    assert {"station_id", "total_settle", "data_time", "project_name", "name", "area"} <= new_cols

    from app.nl2sql.validator import SQLValidator

    sql = (
        "SELECT f.station_id, f.station_name, s.area, f.data_time, f.total_settle "
        "FROM t_data_wash_fcb AS f JOIN t_station AS s ON f.project_name = s.name "
        "WHERE f.data_time >= CURRENT_DATE AND s.area LIKE '%朝阳区%'"
    )
    # station_name 未在 linked columns 中 → 应收窄后拒绝；先补进 new_cols 模拟全量已知列交集场景
    ok, reason = SQLValidator().validate_identifiers(
        sql,
        allowed_tables=new_tables,
        allowed_columns=new_cols | {"station_name"},
    )
    assert ok, reason


def test_link_schema_fcb_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from pathlib import Path

    from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile

    profile = get_nl2sql_business_profile()
    assert profile is not None
    assets = load_semantic_assets(
        str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    )
    intent = QuestionIntent(
        raw_question="监测点沉降",
        scope_question="监测点沉降",
        time_window=None,
        scope=QuestionScopeIntent(),
    )
    binding = align_semantics("监测点沉降", intent, assets=assets)
    assert binding is not None
    table_columns = {
        "t_data_wash_fcb": {"total_settle", "data_time", "station_id", "project_name"},
        "t_station": {"name", "area"},
    }
    linked = link_schema(
        "监测点沉降",
        intent,
        binding,
        table_columns,
        allowlist=set(table_columns.keys()),
        assets=assets,
    )
    assert linked.status in ("ok", "weak")
    assert any(t.name == "t_data_wash_fcb" for t in linked.tables)
    assert any(c.column == "total_settle" for c in linked.columns)


def test_link_schema_gnss(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from pathlib import Path

    from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile

    profile = get_nl2sql_business_profile()
    assets = load_semantic_assets(
        str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    )
    intent = QuestionIntent(
        raw_question="GNSS三维位移",
        scope_question="GNSS三维位移",
        time_window=None,
        scope=QuestionScopeIntent(device_type="gnss"),
    )
    binding = align_semantics("GNSS三维位移", intent, assets=assets)
    table_columns = {
        "t_data_wash_gnss": {"displacement_3d", "data_time", "station_id", "project_name"},
    }
    linked = link_schema(
        "GNSS三维位移",
        intent,
        binding,
        table_columns,
        allowlist=set(table_columns.keys()),
        assets=assets,
    )
    assert any(t.name == "t_data_wash_gnss" for t in linked.tables)
    assert any(c.column == "displacement_3d" for c in linked.columns)


def test_link_schema_station_catalog_links_all_columns_including_lon_lat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """站点清单问句：主表 t_station，全列链接（含 lon/lat），且不灌入层位0标过滤。"""
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile
    from app.nl2sql.schema_linker import narrow_validation_sets
    from app.nl2sql.semantic_layer import is_station_catalog_question

    q = "请帮我查询北京地面沉降监测监测站点有哪些，并分析为什么这样布置监测站点"
    assert is_station_catalog_question(q)

    profile = get_nl2sql_business_profile()
    assert profile is not None
    assets = load_semantic_assets(
        str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    )
    intent = QuestionIntent(
        raw_question=q,
        scope_question=q,
        time_window=None,
        scope=QuestionScopeIntent(),
    )
    binding = align_semantics(q, intent, assets=assets)
    assert binding is not None
    assert binding.default_table == "t_station"
    assert "station_catalog_query" in binding.warnings
    assert not binding.preferred_station_names

    table_columns = {
        "t_station": {"id", "name", "code", "lon", "lat", "area"},
        "t_data_wash_fcb": {
            "total_settle",
            "data_time",
            "station_id",
            "station_name",
            "project_name",
        },
    }
    linked = link_schema(
        q,
        intent,
        binding,
        table_columns,
        allowlist=set(table_columns.keys()),
        assets=assets,
    )
    assert linked.tables[0].name == "t_station"
    station_cols = {c.column for c in linked.columns if c.table == "t_station"}
    assert {"id", "name", "code", "lon", "lat", "area"} <= station_cols
    assert not any(
        f.get("source") in {"fcb_layer0", "fcb_compress"} for f in linked.suggested_filters
    )

    _tables, allowed_cols, _tc = narrow_validation_sets(
        linked,
        set(table_columns),
        {c for cols in table_columns.values() for c in cols},
        table_columns,
        mode="linked_only",
    )
    assert "lon" in allowed_cols and "lat" in allowed_cols

