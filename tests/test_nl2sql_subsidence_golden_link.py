"""地降黄金集：语义对齐 + Schema 链接主表选对（不连库）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache, get_nl2sql_business_profile
from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
from app.nl2sql.schema_linker import link_schema
from app.nl2sql.scope_parser_subsidence import parse_scope_subsidence
from app.nl2sql.semantic_layer import align_semantics, clear_semantic_assets_cache, load_semantic_assets

GOLDEN = Path(__file__).resolve().parents[1] / "configs/nl2sql_business/subsidence/eval/golden_set.json"

_FULL_COLS = {
    "t_data_wash_fcb": {"total_settle", "data_time", "station_id", "station_name", "project_name"},
    "t_data_wash_jyb": {"total_settle", "data_time", "station_id", "station_name", "project_name"},
    "t_data_wash_gnss": {"displacement_2d", "displacement_3d", "data_time", "station_id", "project_name"},
    "t_data_wash_dxswj": {"deep", "elevation", "data_time", "station_id", "project_name"},
    "t_data_wash_kxsylj": {"pressure", "data_time", "project_name"},
    "t_data_wash_gq": {"total_settle", "data_time", "project_name"},
    "t_data_wash_qxz": {"temp", "real_time_rain", "data_time", "project_name"},
    "t_station": {"name", "code", "area", "lon", "lat"},
}


@pytest.fixture(autouse=True)
def _clear(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()
    yield
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()


def _cases() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_golden_primary_table_link(case: dict) -> None:
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = Path(__file__).resolve().parents[1]
    assets = load_semantic_assets(str((root / profile.semantic_dict_path).resolve()))
    q = case["question"]
    scope = parse_scope_subsidence(q)
    intent = QuestionIntent(raw_question=q, scope_question=q, time_window=None, scope=scope)
    binding = align_semantics(q, intent, assets=assets)
    assert binding is not None

    expect_scope = case.get("expect_scope") or {}
    if "station_id" in expect_scope:
        assert scope.station_id == expect_scope["station_id"]
    if "station_name" in expect_scope:
        assert scope.station_name == expect_scope["station_name"]

    expect_metric = case.get("expect_metric")
    if expect_metric:
        assert any(m.id == expect_metric for m in binding.metrics), (
            f"{case['id']}: expected metric {expect_metric}, got {[m.id for m in binding.metrics]}"
        )

    linked = link_schema(
        q,
        intent,
        binding,
        _FULL_COLS,
        allowlist=set(_FULL_COLS.keys()),
        assets=assets,
    )
    assert linked.status in ("ok", "weak"), f"{case['id']} link status={linked.status} reason={linked.fail_reason}"
    linked_names = {t.name for t in linked.tables}
    primary = (case.get("expect_tables") or [None])[0]
    if primary:
        assert primary in linked_names, f"{case['id']}: expect primary {primary} in {linked_names}"
