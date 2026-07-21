import pytest

from app.nl2sql.chain import NL2SQLChain
from app.nl2sql.schema_service import TableColumn, TableSchema
from app.nl2sql.validator import SQLValidator


class _FakeSchema:
    def __init__(self, tables: list[TableSchema]) -> None:
        self._tables = tables

    def list_tables(self) -> list[TableSchema]:
        return self._tables


def _build_chain_for_unit() -> NL2SQLChain:
    chain = object.__new__(NL2SQLChain)
    chain._validator = SQLValidator()
    chain._tidb_forbidden_aliases = set(NL2SQLChain._tidb_forbidden_aliases_default)
    chain._schema = _FakeSchema(
        [
            TableSchema(
                name="monitor_hotarea_temp",
                columns=[TableColumn("id", "BIGINT"), TableColumn("boiler_id", "BIGINT"), TableColumn("point_id", "BIGINT")],
                foreign_keys=[("boiler_id", "account_boiler", "id"), ("point_id", "base_temp_point", "id")],
            ),
            TableSchema(
                name="account_boiler",
                columns=[TableColumn("id", "BIGINT"), TableColumn("boiler_name", "VARCHAR")],
                foreign_keys=[],
            ),
            TableSchema(
                name="base_temp_point",
                columns=[TableColumn("id", "BIGINT"), TableColumn("point_name", "VARCHAR")],
                foreign_keys=[],
            ),
        ]
    )
    return chain


def test_tidb_rewrite_alias_and_postgres_interval() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT t.temp AS load, t.ts AS row_number FROM monitor_hotarea_temp t "
        "WHERE t.ts >= NOW() - INTERVAL '7 days'"
    )
    rewritten, notes = chain._rewrite_tidb_compatible_sql(sql)
    assert " AS load_alias" in rewritten
    assert " AS row_number_alias" in rewritten
    assert "INTERVAL 7 DAY" in rewritten
    assert notes


def test_tidb_validate_forbidden_alias() -> None:
    chain = _build_chain_for_unit()
    ok, reason = chain._validate_tidb_dialect("SELECT temp AS load FROM monitor_hotarea_temp")
    assert not ok
    assert reason is not None
    assert "forbidden alias" in reason


def test_tidb_validate_forbidden_window_function() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT LAG(temp) OVER (PARTITION BY boiler_id ORDER BY ts) AS prev_temp FROM monitor_hotarea_temp"
    ok, reason = chain._validate_tidb_dialect(sql)
    assert not ok
    assert reason is not None
    assert "window functions" in reason


def test_tidb_validate_forbidden_postgres_interval() -> None:
    chain = _build_chain_for_unit()
    ok, reason = chain._validate_tidb_dialect(
        "SELECT * FROM monitor_hotarea_temp WHERE ts >= NOW() - INTERVAL '7 days'"
    )
    assert not ok
    assert reason is not None
    assert "postgres interval" in reason.lower()


def test_tidb_forbidden_aliases_env_extend(monkeypatch) -> None:
    monkeypatch.setenv("NL2SQL_TIDB_FORBIDDEN_ALIASES", "foo_alias,bar_alias")
    chain = _build_chain_for_unit()
    chain._tidb_forbidden_aliases = chain._load_tidb_forbidden_aliases_from_env()
    ok, reason = chain._validate_tidb_dialect("SELECT temp AS foo_alias FROM monitor_hotarea_temp")
    assert not ok
    assert reason is not None
    assert "foo_alias" in reason


def test_rewrite_recent_week_time_window() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp "
        "WHERE event_time BETWEEN '2024-01-01 00:00:00' AND '2024-01-07 23:59:59'"
    )
    rewritten, notes = chain._rewrite_query_filters(sql, question="请分析近一周超温原因")
    assert "DATE_SUB(NOW(), INTERVAL 7 DAY)" in rewritten
    assert "NOW()" in rewritten
    assert notes


def test_rewrite_dynamic_time_window_does_not_corrupt_nested_date_sub() -> None:
    """SQL 已含 DATE_SUB 表达式时仅改写字面量，不截断嵌套括号。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT 1 FROM monitor_hotarea_temp mht "
        "WHERE mht.start_time >= DATE_SUB(NOW(), INTERVAL 7 DAY) ORDER BY mht.start_time DESC"
    )
    rewritten, _notes = chain._rewrite_query_filters(sql, question="请分析最近一周超温情况")
    assert ", INTERVAL 7 DAY), INTERVAL" not in rewritten
    assert rewritten.count("INTERVAL 7 DAY") == 1
    assert "mht.start_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)" in rewritten


def test_rewrite_region_equals_to_like() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE area = 'front wall'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="analyze front wall overheat")
    assert "area LIKE '%front wall%'" in rewritten
    assert notes


def test_rewrite_today_time_window() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE event_time >= '2024-01-01 00:00:00'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="今天超温情况")
    assert "event_time >= CURDATE()" in rewritten
    assert notes


def test_rewrite_last_year_time_window() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE collect_time BETWEEN '2023-01-01' AND '2023-12-31'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="请分析去年超温趋势")
    assert "DATE_SUB(DATE_FORMAT(CURDATE(), '%Y-01-01'), INTERVAL 1 YEAR)" in rewritten
    assert "DATE_FORMAT(CURDATE(), '%Y-01-01')" in rewritten
    assert notes


def test_rewrite_recent_30_days_time_window() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE ts = '2024-02-01'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="recent 30 days overheat")
    assert "DATE_SUB(NOW(), INTERVAL 30 DAY)" in rewritten
    assert "ts <" in rewritten
    assert notes


def test_rewrite_today_qa_literal_half_open() -> None:
    """QA 存库为固定日期字面量时，问句「今天」应改写为自然日半开区间。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2026-05-27 00:00:00' "
        "AND t.start_time <= '2026-05-27 23:59:59'"
    )
    q = "请分析1号锅炉今天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(sql, question=q, time_intent_source=q)
    assert "t.start_time >= CURDATE()" in rewritten
    assert "t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert "23:59:59" not in rewritten
    assert notes


def test_rewrite_today_qa_literal_lt_upper_bound() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2026-05-27 00:00:00' "
        "AND t.start_time < '2026-05-28 00:00:00'"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql, question="今天超温", time_intent_source="今天超温"
    )
    assert "t.start_time >= CURDATE()" in rewritten
    assert "t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert "2026-05-28" not in rewritten
    assert notes


def test_rewrite_plan_near_year_overrides_today() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.record_time >= '2020-01-01 00:00:00' "
        "AND t.record_time <= '2026-05-27 23:59:59'"
    )
    q = "请分析1号锅炉今天的超温。查询用户指定锅炉近一年内的检修记录"
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=q, time_intent_source="请分析1号锅炉今天的超温"
    )
    assert "DATE_SUB(CURDATE(), INTERVAL 1 YEAR)" in rewritten
    assert "record_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert "CURDATE()" in rewritten
    assert "record_time >= CURDATE()" not in rewritten
    assert notes


def test_rewrite_entity_scope_boiler_from_question() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "JOIN account_boiler ab ON t.boiler_id = ab.id "
        "WHERE ab.boiler_name = '2号锅炉'"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql, question="请分析3号锅炉今天的超温", time_intent_source="请分析3号锅炉今天的超温"
    )
    assert "ab.boiler_name = '3号锅炉'" in rewritten
    assert "entity_scope_boiler_name" in notes


@pytest.mark.parametrize(
    "question,expected",
    [
        ("请分析2号锅炉昨天的超温", "2号锅炉"),
        ("请分析2号机组昨天的超温", "2号锅炉"),
        ("请分析2#机组昨天的超温", "2号锅炉"),
        ("请分析#2机组昨天的超温", "2号锅炉"),
        ("请分析二号机组昨天的超温", "2号锅炉"),
        ("请分析一号锅炉昨天的超温", "1号锅炉"),
        ("请分析一号机组昨天的超温", "1号锅炉"),
        ("1号炉当前负荷是多少", "1号锅炉"),
        ("请分析一号炉昨天的超温", "1号锅炉"),
        ("请分析2号炉昨天的超温", "2号锅炉"),
    ],
)
def test_extract_boiler_scope_label_unit_aliases_to_boiler(question: str, expected: str) -> None:
    chain = _build_chain_for_unit()
    assert chain._extract_boiler_scope_label_from_question(question) == expected


def test_extract_boiler_scope_label_does_not_match_lutang() -> None:
    """无序号的「炉膛」不得误判为锅炉；带序号的「1号炉膛」应归一为 1号锅炉。"""
    chain = _build_chain_for_unit()
    assert chain._extract_boiler_scope_label_from_question("炉膛负压偏高怎么办") is None
    assert chain._extract_boiler_scope_label_from_question("1号炉膛温度") == "1号锅炉"


def test_rewrite_entity_scope_cn_boiler_index_to_arabic() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id "
        "WHERE ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%')"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql,
        question="请分析一号锅炉昨天的超温情况",
        time_intent_source="请分析一号锅炉昨天的超温情况",
    )
    assert "'1号锅炉'" in rewritten
    assert "一号锅炉" not in rewritten
    assert "unit_keyword_placeholder_single" in notes


def test_rewrite_entity_scope_unit_alias_rewrites_boiler_sql(question: str = "请分析2号机组昨天的超温") -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id "
        "WHERE ab.boiler_name LIKE CONCAT('%', '1号锅炉', '%')"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=question, time_intent_source=question
    )
    assert "'2号锅炉'" in rewritten
    assert "1号锅炉" not in rewritten
    assert "entity_scope_boiler_name" in notes


def test_entity_scope_uses_time_intent_not_rag_guide_boiler_example() -> None:
    """plan 长问句尾部规则线索含「1号锅炉」示例时，仍以 req.query 的 2号机组 为准。"""
    chain = _build_chain_for_unit()
    user_q = "请分析2号机组昨天的超温情况，并出具分析报告"
    long_q = (
        f"{user_q}。统计用户指定锅炉在昨天的超温事件。"
        "若用户未指定机组/区域，则不要在 WHERE 中臆造具体锅炉名或墙别。"
        "。请结合以下规则线索：参考1号锅炉典型超温案例，注意壁温测点配置。"
    )
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id "
        "WHERE ab.boiler_name LIKE CONCAT('%', '1号锅炉', '%')"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=long_q, time_intent_source=user_q
    )
    assert "'2号锅炉'" in rewritten
    assert "1号锅炉" not in rewritten
    assert "entity_scope_boiler_name" in notes
    assert chain._extract_boiler_scope_label_from_question(long_q) == "1号锅炉"
    assert (
        chain._resolve_entity_scope_question(question=long_q, time_intent_source=user_q)
        == user_q
    )


def test_rewrite_entity_scope_boiler_like_concat_global() -> None:
    """QA strict replay 常见 LIKE CONCAT('%', '1号锅炉', '%') 应随问句替换为 2号锅炉。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id "
        "WHERE ab.boiler_name LIKE CONCAT('%', '1号锅炉', '%') "
        "AND t.pi_code IN ("
        "SELECT DISTINCT t_evt.pi_code FROM monitor_hotarea_temp t_evt "
        "INNER JOIN account_boiler ab_evt ON t_evt.boiler_id = ab_evt.boiler_id "
        "WHERE ab_evt.boiler_name LIKE CONCAT('%', '1号锅炉', '%'))"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql,
        question="请分析2号锅炉昨天的超温情况，并出具分析报告",
        time_intent_source="请分析2号锅炉昨天的超温情况，并出具分析报告",
    )
    assert rewritten.count("'2号锅炉'") >= 2
    assert "1号锅炉" not in rewritten
    assert "entity_scope_boiler_name" in notes


def _q1_unit_keyword_sql_template() -> str:
    return (
        "SELECT ab.boiler_name FROM monitor_hotarea_temp t "
        "INNER JOIN account_boiler ab ON t.boiler_id = ab.boiler_id "
        "WHERE t.start_time >= DATE_SUB(CURDATE(), INTERVAL 2 DAY) "
        "AND t.start_time < DATE_SUB(CURDATE(), INTERVAL 1 DAY) "
        "AND t.highest_temp > t.limit_temp "
        "AND (@unit_keyword IS NULL OR @unit_keyword = '' "
        "OR ab.boiler_name LIKE CONCAT('%', @unit_keyword, '%'))"
    )


def test_extract_unit_keyword_single_boiler() -> None:
    chain = _build_chain_for_unit()
    assert chain._extract_unit_keyword_from_question("请分析1号锅炉前天的超温") == "1号锅炉"
    assert chain._extract_unit_keyword_from_question("请分析2号机组昨天的超温") == "2号锅炉"


@pytest.mark.parametrize(
    "question",
    [
        "请分析所有锅炉前天的超温情况",
        "请分析全部机组昨天的超温",
        "请分析各锅炉本周超温",
        "请分析全厂锅炉超温",
        "请分析前天超温情况",
        "",
    ],
)
def test_extract_unit_keyword_all_plants_returns_none(question: str) -> None:
    chain = _build_chain_for_unit()
    assert chain._extract_unit_keyword_from_question(question) is None


def test_rewrite_unit_keyword_placeholder_single_boiler() -> None:
    chain = _build_chain_for_unit()
    user_q = "请帮我分析1号锅炉前天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(
        _q1_unit_keyword_sql_template(),
        question=user_q,
        time_intent_source=user_q,
    )
    assert "@unit_keyword" not in rewritten
    assert "'' IS NULL OR '' = ''" not in rewritten
    assert "'1号锅炉' = ''" in rewritten or "'' = '' OR ab.boiler_name LIKE CONCAT('%', '1号锅炉', '%')" in rewritten
    assert "unit_keyword_placeholder_single" in notes


def test_rewrite_unit_keyword_placeholder_all_plants_explicit() -> None:
    chain = _build_chain_for_unit()
    user_q = "请分析所有锅炉前天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(
        _q1_unit_keyword_sql_template(),
        question=user_q,
        time_intent_source=user_q,
    )
    assert "@unit_keyword" not in rewritten
    assert "'' IS NULL OR '' = ''" in rewritten
    assert "unit_keyword_placeholder_all_plants" in notes


def test_rewrite_unit_keyword_placeholder_all_plants_implicit() -> None:
    """未指定机组时 @unit_keyword 落为 ''，等价全厂。"""
    chain = _build_chain_for_unit()
    user_q = "请分析前天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(
        _q1_unit_keyword_sql_template(),
        question=user_q,
        time_intent_source=user_q,
    )
    assert "@unit_keyword" not in rewritten
    assert "'' = ''" in rewritten
    assert "unit_keyword_placeholder_all_plants" in notes


def test_single_boiler_wins_over_full_plant_phrase_in_same_query() -> None:
    chain = _build_chain_for_unit()
    user_q = "请对比1号锅炉与所有机组前天的超温"
    assert chain._extract_unit_keyword_from_question(user_q) == "1号锅炉"


def test_explicit_month_day_wins_over_yesterday_in_rag_plan_question() -> None:
    """用户指定 6月29日 时，plan 长问句 RAG 线索中的「昨日」不得覆盖 SQL 时间窗。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2026-05-01 00:00:00' AND t.start_time < '2026-06-01 00:00:00'"
    )
    user_q = "请帮我分析6月29日的超温情况"
    plan_q = (
        f"{user_q}。统计用户指定时间窗内的超温事件明细。"
        "若用户未指定机组/区域，则不要在 WHERE 中臆造具体锅炉名或墙别。"
        "。请结合以下规则线索：参考昨日典型超温案例，注意壁温测点配置。"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=plan_q, time_intent_source=user_q
    )
    assert "DATE(CONCAT(YEAR(CURDATE()), '-06-29'))" in rewritten
    assert "DATE_ADD(DATE(CONCAT(YEAR(CURDATE()), '-06-29')), INTERVAL 1 DAY)" in rewritten
    assert "DATE_SUB(CURDATE(), INTERVAL 1 DAY)" not in rewritten
    assert any("day_cur_06_29" in n for n in notes)


def test_today_wins_over_iso_date_in_long_plan_question() -> None:
    """plan 长问句含 2026-05-27 等示例日期时，仍应用用户 time_intent 的「今天」。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2026-05-01 00:00:00' AND t.start_time < '2026-06-01 00:00:00'"
    )
    user_q = "请分析1号锅炉今天的超温情况，并出具分析报告"
    plan_q = (
        f"{user_q}。统计用户指定锅炉在2026-05-27超温事件明细与2026-05-28跟踪数据。"
        "若用户未指定机组/区域，则不要在 WHERE 中臆造具体锅炉名或墙别。"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=plan_q, time_intent_source=user_q
    )
    assert "t.start_time >= CURDATE()" in rewritten
    assert "t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert "2026-05-01" not in rewritten
    assert any("today" in n for n in notes)


def test_rewrite_q1_aligns_datesub_now_to_today_window() -> None:
    """同一 SQL 内 DATE_SUB(NOW(), INTERVAL N DAY) 与字面量窗对齐为 today（P2）。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= DATE_SUB(NOW(), INTERVAL 2 DAY) "
        "AND t.highest_temp > t.limit_temp "
        "UNION ALL SELECT * FROM monitor_hotarea_temp t2 "
        "WHERE t2.start_time >= '2026-05-01 00:00:00' AND t2.start_time < '2026-06-01 00:00:00'"
    )
    user_q = "请分析1号锅炉今天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=user_q, time_intent_source=user_q
    )
    assert "t.start_time >= CURDATE()" in rewritten
    assert "t2.start_time >= CURDATE()" in rewritten
    assert "DATE_SUB(NOW(), INTERVAL 2 DAY)" not in rewritten
    assert "2026-05-01" not in rewritten


def test_rewrite_q5a_datesub_literal_uses_curdate_anchor() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT p.record_time FROM overhaul_legacy_problem p "
        "WHERE p.record_time >= DATE_SUB('2026-05-26 23:59:59', INTERVAL 1 YEAR)"
    )
    q = "请分析1号锅炉今天的超温。查询用户指定锅炉近一年内的检修记录"
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=q, time_intent_source="请分析1号锅炉今天的超温"
    )
    assert "DATE_SUB(CURDATE(), INTERVAL 1 YEAR)" in rewritten
    assert "2026-05-26" not in rewritten


def test_rewrite_time_placeholders_t_after() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2026-05-26 00:00:00' "
        "AND t.start_time < '2026-05-27 00:00:00' "
        "AND EXISTS (SELECT 1 FROM monitor_hotarea_temp t2 WHERE t2.start_time >= @t_after)"
    )
    user_q = "请分析1号锅炉今天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=user_q, time_intent_source=user_q
    )
    assert "@t_after" not in rewritten
    assert "time_placeholder_t_after" in notes
    assert "t.start_time >= CURDATE()" in rewritten
    assert ">= DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten


def test_rewrite_injects_missing_upper_bound_for_today() -> None:
    """仅有 >= CURDATE() 的子查询应补齐 < DATE_ADD(CURDATE(), INTERVAL 1 DAY)（q1 lv 子查询）。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= CURDATE() AND t.highest_temp > t.limit_temp"
    )
    user_q = "请分析1号锅炉今天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=user_q, time_intent_source=user_q
    )
    assert "t.start_time >= CURDATE()" in rewritten
    assert "t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten
    assert any("injected_lt" in n for n in notes)


def test_rewrite_injects_upper_bound_per_subquery_not_global() -> None:
    """reg 子查询已有上界时，lv 子查询仍应单独补齐上界（q1）。"""
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM ("
        "SELECT t.boiler_id FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= CURDATE() AND t.highest_temp > t.limit_temp"
        ") lv LEFT JOIN ("
        "SELECT t.boiler_id FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= CURDATE() "
        "AND t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY) AND t.highest_temp > t.limit_temp"
        ") reg ON lv.boiler_id = reg.boiler_id"
    )
    user_q = "请分析1号锅炉今天的超温情况"
    rewritten, notes = chain._rewrite_query_filters(
        sql, question=user_q, time_intent_source=user_q, plan_item_id="q1"
    )
    assert rewritten.count("t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)") == 2
    assert any("injected_lt" in n for n in notes)


def test_rewrite_dedupes_duplicate_upper_bound() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t WHERE t.start_time >= CURDATE() "
        "AND t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY) "
        "AND t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)"
    )
    out = chain._dedupe_redundant_time_upper_bounds(
        sql, end_expr="DATE_ADD(CURDATE(), INTERVAL 1 DAY)"
    )
    assert out.count("t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)") == 1


def test_rewrite_normalizes_end_time_upper_to_start_time() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2026-05-27 00:00:00' "
        "AND t.end_time <= '2026-05-27 23:59:59'"
    )
    user_q = "请分析1号锅炉今天的超温情况"
    rewritten, _ = chain._rewrite_query_filters(
        sql, question=user_q, time_intent_source=user_q, plan_item_id="q6a"
    )
    assert "t.end_time <" not in rewritten.lower()
    assert "t.start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)" in rewritten


def test_rewrite_q2c_group_concat_utf8_safe() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT GROUP_CONCAT(x.测点及位置 ORDER BY x.max_delta DESC SEPARATOR '、') "
        "FROM t"
    )
    rewritten, notes = chain._rewrite_query_filters(
        sql,
        question="请分析1号锅炉今天的超温情况",
        time_intent_source="请分析1号锅炉今天的超温情况",
        plan_item_id="q2c",
    )
    assert "CAST(GROUP_CONCAT(" in rewritten
    assert "utf8mb4" in rewritten
    assert "group_concat_utf8_safe" in notes


def test_table_scope_from_env(monkeypatch) -> None:
    chain = _build_chain_for_unit()
    tc = {
        "monitor_hotarea_temp": {"id", "boiler_id"},
        "account_boiler": {"id", "boiler_name"},
        "base_temp_point": {"id", "point_name"},
    }
    monkeypatch.setenv("ANALYSIS_NL2SQL_TABLE_SCOPE_DEFAULT", "monitor_hotarea_temp,account_boiler")
    scoped = chain._resolve_table_scope(analysis_type="overheat_guidance", table_columns=tc)
    assert scoped == {"monitor_hotarea_temp", "account_boiler"}


def test_join_whitelist_rejects_unknown_join() -> None:
    chain = _build_chain_for_unit()
    tc = {
        "monitor_hotarea_temp": {"id", "boiler_id", "point_id"},
        "account_boiler": {"id", "boiler_name"},
        "base_temp_point": {"id", "point_name"},
    }
    wl = chain._build_join_whitelist(tc, analysis_type="overheat_guidance")
    ok, reason = chain._validate_join_whitelist(
        "SELECT 1 FROM monitor_hotarea_temp t JOIN account_boiler b ON t.id = b.id",
        tc,
        wl,
    )
    assert not ok
    assert reason is not None
    assert "join key not in whitelist" in reason


def test_rewrite_quarter_not_whole_year() -> None:
    chain = _build_chain_for_unit()
    sql = (
        "SELECT * FROM monitor_hotarea_temp t "
        "WHERE t.start_time >= '2025-01-01 00:00:00' AND t.start_time < '2026-01-01 00:00:00'"
    )
    rewritten, notes = chain._rewrite_query_filters(sql, question="2025年第一季度超温统计")
    assert "2025-01-01" in rewritten
    assert "2025-04-01" in rewritten
    assert "2026-01-01" not in rewritten
    assert notes


def test_rewrite_this_quarter_half_open() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE record_time >= '2024-01-01'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="本季度超温情况")
    assert "INTERVAL ((MONTH(CURDATE()) - 1) % 3) MONTH)" in rewritten or "DATE_ADD" in rewritten
    assert notes


def test_rewrite_three_days_ago() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE data_time = '2024-03-01'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="大前天超温记录")
    assert "INTERVAL 3 DAY" in rewritten
    assert notes


def test_rewrite_exact_day() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE start_time >= '2020-01-01'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="2026-05-19超温")
    assert "2026-05-19" in rewritten
    assert "2026-05-20" in rewritten
    assert notes


def test_rewrite_month_only_current_year() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE start_time >= '2020-06-01'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="6月份超温统计")
    assert "YEAR(CURDATE())" in rewritten
    assert "-06-01" in rewritten
    assert notes


def test_rewrite_year_before_last() -> None:
    chain = _build_chain_for_unit()
    sql = "SELECT * FROM monitor_hotarea_temp WHERE start_time BETWEEN '2018-01-01' AND '2018-12-31'"
    rewritten, notes = chain._rewrite_query_filters(sql, question="前年超温对比")
    assert "INTERVAL 2 YEAR" in rewritten
    assert notes

