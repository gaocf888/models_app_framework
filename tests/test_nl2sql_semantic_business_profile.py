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


def test_sql_dialect_adapt_this_week(monkeypatch: pytest.MonkeyPatch) -> None:
    """本周一起点须变为 date_trunc，且不得残留 WEEKDAY()。"""
    from app.nl2sql.nl2sql_business_profile import clear_nl2sql_business_profile_cache
    from app.nl2sql.sql_dialect import scrub_mysql_weekday_for_postgres
    from app.nl2sql.time_intent_display import extract_time_window_from_question

    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    monkeypatch.delenv("NL2SQL_SQL_DIALECT", raising=False)
    clear_nl2sql_business_profile_cache()
    try:
        assert is_postgres_dialect()
        mysql_week = "DATE_SUB(CURDATE(), INTERVAL WEEKDAY(CURDATE()) DAY)"
        out = adapt_mysql_time_expr_to_postgres(mysql_week)
        assert "date_trunc('week'" in out
        assert "WEEKDAY" not in out.upper()

        # CURDATE 先被替换后的半适配残留
        half = "DATE_SUB(CURRENT_DATE, INTERVAL WEEKDAY(CURRENT_DATE) DAY)"
        out2 = adapt_mysql_time_expr_to_postgres(half)
        assert "date_trunc('week'" in out2
        assert "WEEKDAY" not in out2.upper()

        # LLM 常见写法
        llm = "CURRENT_DATE - INTERVAL '1 day' * WEEKDAY(CURRENT_DATE)"
        out3 = adapt_mysql_time_expr_to_postgres(llm)
        assert "date_trunc('week'" in out3
        assert "WEEKDAY" not in out3.upper()

        win = extract_time_window_from_question("请帮我查询朝阳区本周的沉降量")
        assert win is not None
        start, end, tag = win
        assert tag == "this_week"
        from app.nl2sql.sql_dialect import adapt_time_window

        pg_start, pg_end = adapt_time_window(start, end)
        assert "date_trunc('week'" in pg_start
        assert "WEEKDAY" not in pg_start.upper()
        assert "WEEKDAY" not in pg_end.upper()
        assert "INTERVAL" in pg_end

        scrubbed = scrub_mysql_weekday_for_postgres(
            "SELECT 1 WHERE t.data_time >= CURRENT_DATE - INTERVAL '1 day' * WEEKDAY(CURRENT_DATE)"
        )
        assert "date_trunc('week'" in scrubbed
        assert "WEEKDAY" not in scrubbed.upper()
    finally:
        clear_nl2sql_business_profile_cache()


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


def test_semantic_default_device_type_fcb_when_unspecified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    intent = QuestionIntent(
        raw_question="通州区近一年沉降多少",
        scope_question="通州区近一年沉降多少",
        time_window=None,
        scope=QuestionScopeIntent(district="通州区"),
    )
    binding = align_semantics("通州区近一年沉降多少", intent, assets=assets)
    assert binding is not None
    assert "fcb" in binding.device_types
    assert any("fcb" in str(t).lower() or "t_data_wash_fcb" in str(t).lower() for t in binding.device_type_tables)
    assert "default_device_type_fcb" in binding.warnings


def test_semantic_station_catalog_skips_default_fcb_and_layer0(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """站点清单问句：不默认 fcb，不注入全市层位0标编号。"""
    from app.nl2sql.semantic_layer import is_station_catalog_question

    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    q = "北京地面沉降监测站点有哪些"
    assert is_station_catalog_question(q)
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
    assert "default_device_type_fcb" not in binding.warnings
    assert not binding.preferred_station_names
    assert "fcb" not in binding.device_types


def test_semantic_explicit_gnss_not_overridden_by_fcb_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    intent = QuestionIntent(
        raw_question="通州区GNSS位移",
        scope_question="通州区GNSS位移",
        time_window=None,
        scope=QuestionScopeIntent(district="通州区", device_type="gnss"),
    )
    binding = align_semantics("通州区GNSS位移", intent, assets=assets)
    assert binding is not None
    assert "gnss" in binding.device_types
    assert "default_device_type_fcb" not in binding.warnings
    assert not binding.preferred_station_names


def test_fcb_layer_map_loaded_and_layer0_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    assert profile.fcb_layer_map_file
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    assert len(assets.fcb_layer0_by_project) >= 40
    assert assets.fcb_layer0_by_project.get("F8(周村)") == "F8-10"
    assert "F8-10" in assets.fcb_mark_names


def test_align_injects_layer0_preferred_station_for_site(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    intent = QuestionIntent(
        raw_question="周村分层标沉降",
        scope_question="周村分层标沉降",
        time_window=None,
        scope=QuestionScopeIntent(station_name="F8(周村)", station_id="F8", device_type="fcb"),
    )
    binding = align_semantics("周村分层标沉降", intent, assets=assets)
    assert binding is not None
    assert "F8(周村)" in binding.project_names
    assert "F8-10" in binding.preferred_station_names
    assert "fcb_layer0_preferred_station" in binding.warnings


def test_align_injects_layer0_for_district_and_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.nl2sql.question_intent_display import format_parsed_intent_prompt_block
    from app.nl2sql.schema_linker import link_schema

    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    q = "朝阳区分层标沉降多少"
    intent = QuestionIntent(
        raw_question=q,
        scope_question=q,
        time_window=None,
        scope=QuestionScopeIntent(district="朝阳区", device_type="fcb"),
    )
    binding = align_semantics(q, intent, assets=assets)
    assert binding is not None
    assert binding.preferred_station_names
    assert "fcb_layer0_district_filter" in binding.warnings
    # 朝阳层位0应包含常见站代表标
    assert any(m.startswith("F") for m in binding.preferred_station_names)

    table_columns = {
        "t_data_wash_fcb": {"total_settle", "data_time", "station_id", "station_name", "project_name"},
        "t_station": {"name", "area"},
    }
    linked = link_schema(q, intent, binding, table_columns, allowlist=set(table_columns), assets=assets)
    mark_filters = [
        f for f in linked.suggested_filters if f.get("column") == "station_name" and f.get("source") == "fcb_layer0"
    ]
    assert mark_filters
    assert mark_filters[0]["op"] == "in"
    assert "F1-7" in mark_filters[0]["value"] or any(str(v).startswith("F") for v in mark_filters[0]["value"])

    # 站点展示名不应误写入 station_name like 过滤
    bad = [
        f
        for f in linked.suggested_filters
        if f.get("column") == "station_name" and "周村" in str(f.get("value"))
    ]
    assert not bad

    block = format_parsed_intent_prompt_block(intent, semantic=binding.to_dict(), linked_schema=linked.to_dict())
    assert "优选标编号" in block
    assert "建议过滤" in block


def test_align_explicit_mark_not_replaced_by_layer0(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    q = "F8-7 本季度沉降"
    intent = QuestionIntent(
        raw_question=q,
        scope_question=q,
        time_window=None,
        scope=QuestionScopeIntent(station_name="F8(周村)", station_id="F8", device_type="fcb"),
    )
    binding = align_semantics(q, intent, assets=assets)
    assert binding is not None
    assert "F8-7" in binding.preferred_station_names
    assert "F8-10" not in binding.preferred_station_names
    assert "fcb_explicit_mark" in binding.warnings


def test_align_injects_compress_boundary_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    from app.nl2sql.question_intent_display import format_parsed_intent_prompt_block
    from app.nl2sql.schema_linker import link_schema

    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    assert assets.fcb_layers_by_project.get("F8(周村)", {}).get(0) == "F8-10"
    assert assets.fcb_layers_by_project.get("F8(周村)", {}).get(1) == "F8-7"

    q = "F8(周村)本季度第1压缩层沉降"
    intent = QuestionIntent(
        raw_question=q,
        scope_question=q,
        time_window=None,
        scope=QuestionScopeIntent(station_name="F8(周村)", station_id="F8", device_type="fcb"),
    )
    binding = align_semantics(q, intent, assets=assets)
    assert binding is not None
    assert "fcb_compress_boundary_marks" in binding.warnings
    assert {"F8-10", "F8-7"} <= set(binding.preferred_station_names)
    assert any(p.get("layer_from") == 0 and p.get("layer_to") == 1 for p in binding.compress_pairs)
    assert any(p.get("mark_from") == "F8-10" and p.get("mark_to") == "F8-7" for p in binding.compress_pairs)

    table_columns = {
        "t_data_wash_fcb": {"total_settle", "data_time", "station_id", "station_name", "project_name"},
        "t_station": {"name", "area"},
    }
    linked = link_schema(q, intent, binding, table_columns, allowlist=set(table_columns), assets=assets)
    mark_filters = [
        f for f in linked.suggested_filters if f.get("column") == "station_name" and f.get("source") == "fcb_compress"
    ]
    assert mark_filters
    assert mark_filters[0]["op"] == "in"
    assert set(mark_filters[0]["value"]) >= {"F8-10", "F8-7"}

    block = format_parsed_intent_prompt_block(intent, semantic=binding.to_dict(), linked_schema=linked.to_dict())
    assert "压缩层边界标" in block
    assert "compress" in block.lower() or "Δ" in block or "F8-10" in block


def test_align_compress_multi_ordinals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    q = "周村第1、2压缩层"
    intent = QuestionIntent(
        raw_question=q,
        scope_question=q,
        time_window=None,
        scope=QuestionScopeIntent(station_name="F8(周村)", station_id="F8", device_type="fcb"),
    )
    binding = align_semantics(q, intent, assets=assets)
    assert binding is not None
    pairs = {(p["layer_from"], p["layer_to"]) for p in binding.compress_pairs}
    assert (0, 1) in pairs
    assert (1, 2) in pairs
    assert {"F8-10", "F8-7", "F8-4"} <= set(binding.preferred_station_names)


def test_align_compress_without_station_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NL2SQL_BUSINESS_DOMAIN", "subsidence")
    profile = get_nl2sql_business_profile()
    assert profile is not None
    root = str((__import__("pathlib").Path(__file__).resolve().parents[1] / profile.semantic_dict_path).resolve())
    assets = load_semantic_assets(root)
    assert assets is not None
    q = "朝阳区第1压缩层沉降"
    intent = QuestionIntent(
        raw_question=q,
        scope_question=q,
        time_window=None,
        scope=QuestionScopeIntent(district="朝阳区", device_type="fcb"),
    )
    binding = align_semantics(q, intent, assets=assets)
    assert binding is not None
    assert any(w.startswith("fcb_compress_need_station") for w in binding.warnings)
    assert not binding.compress_pairs

