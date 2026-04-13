from __future__ import annotations

from app.nl2sql.schema_snippet_parser import (
    TableRAGHints,
    format_enriched_catalog_line,
    parse_nl2sql_schema_snippets,
)


def test_parse_index_table_and_field_table_with_title() -> None:
    text = """
锅炉信息表（account_boiler）

[DOCX_TABLE rows=2 cols=3]
字段名 | 类型 | 注释
boiler_id | varchar(32) | 主键 id
boiler_name | varchar(128) | 锅炉名称

[DOCX_TABLE rows=2 cols=4]
类别 | 中文描述 | 数据库表名 | 备注
设备台账 | 锅炉基本信息 | account_boiler |
""".strip()
    hints = parse_nl2sql_schema_snippets([text])
    assert "account_boiler" in hints
    h = hints["account_boiler"]
    assert h.zh_label == "锅炉基本信息" or "锅炉" in (h.zh_label or "")
    assert h.column_comments.get("boiler_name") == "锅炉名称"


def test_parse_nl2sql_table_map_line() -> None:
    text = "[NL2SQL_TABLE_MAP] table=monitor_hotarea_temp | zh=超温记录表 | category=受热面超温"
    hints = parse_nl2sql_schema_snippets([text])
    assert hints["monitor_hotarea_temp"].zh_label == "超温记录表"


def test_format_enriched_catalog_line() -> None:
    h = TableRAGHints(zh_label="锅炉", column_comments={"boiler_name": "锅炉名称"})
    line = format_enriched_catalog_line(
        "account_boiler",
        ["boiler_id", "boiler_name"],
        h,
        max_cols=10,
    )
    assert "account_boiler" in line
    assert "锅炉" in line
    assert "boiler_name(锅炉名称)" in line


def test_format_enriched_catalog_line_includes_foreign_keys() -> None:
    line = format_enriched_catalog_line(
        "monitor_hotarea_temp",
        ["id", "boiler_id"],
        None,
        max_cols=10,
        foreign_keys=[("boiler_id", "account_boiler", "boiler_id")],
    )
    assert "FK:" in line
    assert "boiler_id->account_boiler.boiler_id" in line
