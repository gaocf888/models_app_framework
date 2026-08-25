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

