import re

from app.nl2sql.entity_rules import EntityRule, check_entity_rules
from app.nl2sql.validator import SQLValidator


def test_validate_sql_in_markdown_code_fence() -> None:
    validator = SQLValidator()
    sql = """```sql
SELECT temperature, date_time
FROM base_temp_device
WHERE temp_type = '高温过热器'
ORDER BY temperature DESC;
```"""
    assert validator.validate(sql)
    assert validator.normalize_sql(sql).startswith("SELECT")


def test_validate_cte_select_sql() -> None:
    validator = SQLValidator()
    sql = "WITH t AS (SELECT 1 AS a) SELECT a FROM t;"
    assert validator.validate(sql)


def test_normalize_sql_collapses_whitespace_outside_strings() -> None:
    v = SQLValidator()
    raw = """SELECT *
FROM monitor_hotarea_temp
WHERE boiler_id = '1'
ORDER BY highest_temp DESC;"""
    norm = v.normalize_sql(raw)
    assert "\n" not in norm
    assert norm.startswith("SELECT * FROM monitor_hotarea_temp WHERE")
    assert "ORDER BY highest_temp DESC" in norm


def test_normalize_sql_preserves_newline_inside_string_literal() -> None:
    v = SQLValidator()
    raw = "SELECT 1 FROM t WHERE x = 'a\nb'"
    norm = v.normalize_sql(raw)
    assert "SELECT 1 FROM t WHERE x = " in norm
    assert "'a\nb'" in norm


def test_normalize_sql_preserves_doubled_single_quote_in_string() -> None:
    v = SQLValidator()
    raw = "SELECT 1\nFROM t\nWHERE x = 'it''s ok'"
    norm = v.normalize_sql(raw)
    assert "it''s ok" in norm
    assert "\n" not in norm


def test_validate_identifiers_reject_unknown_table() -> None:
    validator = SQLValidator()
    sql = "SELECT * FROM temperature_record t JOIN account_boiler b ON t.boiler_id = b.boiler_id"
    ok, reason = validator.validate_identifiers(
        sql,
        allowed_tables={"base_temp_device", "account_boiler"},
        allowed_columns={"boiler_id"},
    )
    assert not ok
    assert reason is not None
    assert "unknown tables" in reason


def test_parse_table_aliases_simple_join() -> None:
    v = SQLValidator()
    sql = "SELECT a.id FROM orders a JOIN orders b ON a.id = b.user_id"
    m = v.parse_table_aliases_from_sql(sql)
    assert m.get("a") == "orders"
    assert m.get("b") == "orders"


def test_validate_column_table_binding_rejects_wrong_column_for_alias() -> None:
    v = SQLValidator()
    tc = {"orders": {"id", "user_id", "amount", "created_at"}}
    sql = "SELECT a.current_a FROM orders a"
    ok, reason = v.validate_column_table_binding(sql, table_columns=tc)
    assert not ok
    assert reason is not None
    assert "current_a" in reason


def test_validate_column_table_binding_ignores_literal_dot_pattern() -> None:
    v = SQLValidator()
    tc = {"orders": {"id", "user_id"}}
    sql = "SELECT id FROM orders o WHERE note = 'a.b'"
    ok, _ = v.validate_column_table_binding(sql, table_columns=tc)
    assert ok


def test_entity_rule_hits_when_question_and_sql_match() -> None:
    pat = re.compile(r"(?i)mill_name\s*=\s*'[^']*一号锅炉", re.DOTALL)
    rules = [
        EntityRule(
            question_contains_any=("一号锅炉",),
            sql_pattern=pat,
            message="mill_name 不应绑定锅炉名称",
        )
    ]
    ok, msg = check_entity_rules(
        "查一号锅炉负荷",
        "SELECT * FROM base_coal_mill WHERE mill_name = '一号锅炉'",
        rules,
    )
    assert not ok
    assert msg is not None
    assert "mill_name" in msg


def test_entity_rule_skips_when_question_has_no_keyword() -> None:
    pat = re.compile(r"mill_name", re.I)
    rules = [
        EntityRule(
            question_contains_any=("一号锅炉",),
            sql_pattern=pat,
            message="x",
        )
    ]
    ok, _ = check_entity_rules("查磨煤机", "SELECT * FROM t WHERE mill_name = '一号锅炉'", rules)
    assert ok

