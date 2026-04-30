from app.inspection_v2.chunk_table_filter import (
    chunk_contains_table,
    filter_table_work_items,
)


def test_docx_v2_requires_docx_table_marker() -> None:
    assert chunk_contains_table("[DOCX_V2_TABLE idx=1]\nr1:\tx", parse_route="docx_v2") is True
    assert chunk_contains_table("plain text only", parse_route="docx_v2") is False


def test_legacy_requires_multiple_pipe_lines() -> None:
    assert chunk_contains_table("a|b\nc|d", parse_route="docx") is True
    assert chunk_contains_table("only | one line", parse_route="text") is False


def test_filter_renumbers_work_idx() -> None:
    chunks = ["plain", "[DOCX_V2_TABLE idx=1]\nr1:\tx", "also plain"]
    items = filter_table_work_items(chunks, parse_route="docx_v2")
    assert len(items) == 1
    assert items[0][0] == 1
    assert "DOCX_V2_TABLE" in items[0][1]
