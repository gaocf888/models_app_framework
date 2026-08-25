"""Schema 链接补充：catalog 收窄、失败状态、锅炉 domain 回退。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache
from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
from app.nl2sql.schema_linker import (
    LinkedSchema,
    LinkedTable,
    filter_catalog_tables_by_linked_schema,
    link_schema,
)
from app.nl2sql.semantic_layer import align_semantics, clear_semantic_assets_cache, load_semantic_assets


@pytest.fixture(autouse=True)
def _clear() -> None:
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()
    yield
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()


def _load_subsidence_assets():
    from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile
    from pathlib import Path

    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = Path(__file__).resolve().parents[1]
    return load_semantic_assets(str((root / profile.semantic_dict_path).resolve()))


def test_filter_catalog_linked_only_excludes_unlinked() -> None:
    catalog = [
        SimpleNamespace(name="t_data_wash_fcb"),
        SimpleNamespace(name="t_data_wash_gnss"),
        SimpleNamespace(name="secret_other_table"),
    ]
    linked = LinkedSchema(
        tables=[LinkedTable(name="t_data_wash_fcb", reason="primary", score=1.0)],
        status="ok",
    )
    out = filter_catalog_tables_by_linked_schema(
        catalog, linked, mode="linked_only", full_table_names={t.name for t in catalog}
    )
    names = {t.name for t in out}
    assert names == {"t_data_wash_fcb"}
    assert "secret_other_table" not in names


def test_link_failed_when_no_allowlist_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    assets = _load_subsidence_assets()
    intent = QuestionIntent(
        raw_question="监测点沉降",
        scope_question="监测点沉降",
        time_window=None,
        scope=QuestionScopeIntent(),
    )
    binding = align_semantics("监测点沉降", intent, assets=assets)
    assert binding is not None
    linked = link_schema(
        "监测点沉降",
        intent,
        binding,
        table_columns={"unrelated_table": {"x"}},
        allowlist={"unrelated_table"},
        assets=assets,
    )
    # 无白名单交集时链接应失败或极弱
    assert linked.status in ("failed", "weak")
    if linked.status == "failed":
        assert linked.fail_reason


def test_boiler_domain_profile_disables_semantic_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "boiler_four_tube")
    monkeypatch.delenv("NL2SQL_SEMANTIC_LINK_ENABLED", raising=False)
    from app.core.config import get_app_config
    from app.nl2sql.intent_config import semantic_link_enabled
    from app.nl2sql.nl2sql_business_profile import get_nl2sql_business_profile

    clear_nl2sql_business_profile_cache()
    cfg = get_app_config().nl2sql_intent
    assert cfg.business_domain == "boiler_four_tube"
    assert semantic_link_enabled() is False
    profile = get_nl2sql_business_profile()
    assert profile is not None
    assert "account_boiler" in profile.table_allowlist
