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

