"""NL2SQL 语义层与业务配置包测试。"""

from __future__ import annotations

import os

import pytest

from app.core.config import get_app_config
from app.core.config import get_app_config
from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache, get_nl2sql_business_profile
from app.nl2sql.question_scope_models import QuestionIntent, QuestionScopeIntent
from app.nl2sql.scope_parser_subsidence import parse_scope_subsidence
from app.nl2sql.semantic_layer import align_semantics, clear_semantic_assets_cache, load_semantic_assets
from app.nl2sql.sql_cache import compute_nl2sql_policy_fp
from app.nl2sql.sql_dialect import adapt_mysql_time_expr_to_postgres, is_postgres_dialect


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()
    yield
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    clear_semantic_assets_cache()


def test_subsidence_profile_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    assert profile.business_domain == "subsidence"
    assert "t_data_wash_fcb" in profile.table_allowlist
    assert profile.sql_dialect == "postgres"
    assert profile.semantic_link_enabled is True


def test_semantic_align_subsidence_question(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    intent = QuestionIntent(
        raw_question="朝阳区分层标沉降多少",
        scope_question="朝阳区分层标沉降多少",
        time_window=None,
        scope=QuestionScopeIntent(district="朝阳区", device_type="fcb"),
    )
    binding = align_semantics("朝阳区分层标沉降多少", intent, assets=assets)
    assert binding is not None
    assert any(m.id == "period_subsidence_mm" for m in binding.metrics)
    assert "fcb" in binding.device_types
    assert "朝阳区" in binding.district_codes


def test_parse_scope_subsidence_district(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    scope = parse_scope_subsidence("通州区GNSS位移")
    assert scope.district == "通州区"
    assert scope.device_type == "gnss"


def test_parse_scope_subsidence_station_alias_and_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    by_alias = parse_scope_subsidence("周村分层标沉降")
    assert by_alias.station_name == "F8(周村)"
    assert by_alias.station_id == "F8"
    by_id = parse_scope_subsidence("查询 HSL01 最新沉降")
    assert by_id.station_id == "HSL01"
    by_name = parse_scope_subsidence("大兴机场北沉降情况")
    assert by_name.station_name == "大兴机场北"
    assert by_name.station_id == "JCBZ"


def test_sql_dialect_adapt_yesterday(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.delenv("NL2SQL_SQL_DIALECT", raising=False)
    assert is_postgres_dialect()
    out = adapt_mysql_time_expr_to_postgres("DATE_SUB(CURDATE(), INTERVAL 1 DAY)")
    assert "CURRENT_DATE" in out
    assert "INTERVAL" in out


def test_sql_dialect_adapt_quarter_and_concat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    this_q = (
        "DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-%m-01'), "
        "INTERVAL ((MONTH(CURDATE()) - 1) % 3) MONTH)"
    )
    out = adapt_mysql_time_expr_to_postgres(this_q)
    assert "date_trunc('month'" in out
    assert "INTERVAL '1 month'" in out
    month_start = adapt_mysql_time_expr_to_postgres(
        "DATE(CONCAT(YEAR(CURDATE()), '-04-01'))"
    )
    assert "make_date" in month_start
    assert "4" in month_start


def test_policy_fp_includes_semantic_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    fp1 = compute_nl2sql_policy_fp(analysis_type=None)
    assert fp1
    # 模拟语义版本变化应导致指纹变化（通过 monkeypatch semantic_version_fingerprint）
    from app.nl2sql import semantic_layer

    original = semantic_layer.semantic_version_fingerprint

    monkeypatch.setattr(semantic_layer, "semantic_version_fingerprint", lambda: "2026.08.24")
    fp_a = compute_nl2sql_policy_fp(analysis_type=None)
    monkeypatch.setattr(semantic_layer, "semantic_version_fingerprint", lambda: "2099.01.01")
    fp_b = compute_nl2sql_policy_fp(analysis_type=None)
    monkeypatch.setattr(semantic_layer, "semantic_version_fingerprint", original)
    assert fp_a != fp_b


def test_format_parsed_intent_includes_semantic_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.nl2sql.question_intent_display import format_parsed_intent_prompt_block

    intent = QuestionIntent(
        raw_question="朝阳区分层标沉降",
        scope_question="朝阳区分层标沉降",
        time_window=None,
        scope=QuestionScopeIntent(district="朝阳区", device_type="fcb"),
    )
    block = format_parsed_intent_prompt_block(
        intent,
        semantic={
            "version": "2026.08.24",
            "metrics": [
                {
                    "id": "period_subsidence_mm",
                    "name": "周期沉降量",
                    "unit": "mm",
                    "preferred_tables": ["t_data_wash_fcb"],
                    "preferred_columns": ["total_settle"],
                }
            ],
            "warnings": ["demo_warning"],
        },
        linked_schema={
            "status": "ok",
            "tables": [{"name": "t_data_wash_fcb", "reason": "primary"}],
        },
    )
    assert "语义版本" in block
    assert "period_subsidence_mm" in block
    assert "t_data_wash_fcb" in block
    assert "demo_warning" in block
    assert "链接主表" in block



def test_config_merges_subsidence_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    # 清除显式 DB_*，验证 profile.db.* 注入
    for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_URL", "NL2SQL_SQL_DIALECT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_PASSWORD", "test-secret")
    cfg = get_app_config().nl2sql_intent
    assert cfg.business_domain == "subsidence"
    assert cfg.semantic_link_enabled is True
    assert cfg.sql_dialect == "postgres"
    db = get_app_config().db
    assert db.dialect == "postgres"
    assert db.host == "192.169.237.197"
    assert db.port == 5432
    assert db.database == "dmcj"
    assert "postgresql+asyncpg" in db.url
    assert "charset=" not in db.url


def test_config_merges_boiler_profile_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "boiler_four_tube")
    for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_URL", "NL2SQL_SQL_DIALECT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DB_PASSWORD", "test-secret")
    get_app_config.cache_clear()
    clear_nl2sql_business_profile_cache()
    db = get_app_config().db
    assert db.dialect == "tidb"
    assert db.host == "192.168.90.62"
    assert db.port == 4000
    assert "mysql+aiomysql" in db.url
    assert "charset=utf8mb4" in db.url

