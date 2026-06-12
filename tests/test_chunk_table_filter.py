from app.inspection_v2.chunk_table_filter import (
    chunk_contains_table,
    extract_docx_v2_table_blocks_for_llm,
    filter_table_work_items,
    resolve_llm_parse_chunk_body,
    strip_trailing_empty_columns_for_llm,
)

SAMPLE_TABLE_WITH_EMPTY_C4 = "\n".join(
    [
        "[DOCX_V2_TABLE idx=2 rows=15 cols=5]",
        "r0: c0-c3='水冷壁螺旋段左墙_吹灰孔39'[hmerge×4] | c4=''",
        "r1: c0-c1='上'[hmerge×2] | c2-c3='下'[hmerge×2] | c4=''",
        "r2: c0='4' | c1='6.8' | c2='3' | c3='5.0'[颜色标注:高亮=red] | c4=''",
        "r3: c0='5' | c1='6.3' | c2='4' | c3='7.3' | c4=''",
        "r10: c0='' | c1='' | c2='11' | c3='6.1' | c4=''",
        "r14: c0='' | c1='' | c2='' | c3='' | c4=''",
    ]
)


def test_docx_v2_requires_docx_table_marker() -> None:
    assert chunk_contains_table("[DOCX_V2_TABLE idx=1]\nr1:\tx", parse_route="docx_v2") is True
    assert chunk_contains_table("plain text only", parse_route="docx_v2") is False


def test_legacy_requires_multiple_pipe_lines() -> None:
    assert chunk_contains_table("a|b\nc|d", parse_route="docx") is True
    assert chunk_contains_table("only | one line", parse_route="text") is False


def test_extract_docx_v2_table_blocks_strips_heading_and_prelude() -> None:
    chunk = "\n".join(
        [
            "[处理单元 heading_path=（一）炉膛水冷壁检查情况]",
            "低再第二层测厚数据低于3.15mm超标，共超标21根",
            "[DOCX_V2_TABLE idx=1 rows=2 cols=2]",
            "r0: c0='水冷壁右墙' | c1='1'",
            "r1: c0='7.4' | c1='2'",
        ]
    )
    out = extract_docx_v2_table_blocks_for_llm(chunk)
    assert out.startswith("[DOCX_V2_TABLE idx=1")
    assert "处理单元" not in out
    assert "低再第二层" not in out
    assert "r0:" in out and "r1:" in out


def test_strip_trailing_empty_columns_updates_cols_and_removes_c4() -> None:
    out = strip_trailing_empty_columns_for_llm(SAMPLE_TABLE_WITH_EMPTY_C4)
    assert "cols=4]" in out or "cols=4 " in out
    assert "c4=" not in out
    assert "cols=5" not in out
    assert "c0-c3='水冷壁螺旋段左墙_吹灰孔39'[hmerge×4]" in out
    assert "[颜色标注:高亮=red]" in out
    assert "r1: c0-c1='上'[hmerge×2] | c2-c3='下'[hmerge×2]" in out


def test_strip_trailing_empty_columns_keeps_table_when_c4_has_content() -> None:
    table = "\n".join(
        [
            "[DOCX_V2_TABLE idx=1 rows=2 cols=5]",
            "r0: c0='a' | c1='b' | c2='c' | c3='d' | c4='note'",
            "r1: c0='1' | c1='2' | c2='3' | c3='4' | c4=''",
        ]
    )
    out = strip_trailing_empty_columns_for_llm(table)
    assert "cols=5" in out
    assert "c4='note'" in out


def test_strip_trailing_empty_columns_keeps_col_with_color_only() -> None:
    table = "\n".join(
        [
            "[DOCX_V2_TABLE idx=1 rows=2 cols=3]",
            "r0: c0='a' | c1='b' | c2=''[颜色标注:高亮=red]",
            "r1: c0='1' | c1='2' | c2=''",
        ]
    )
    out = strip_trailing_empty_columns_for_llm(table)
    assert "cols=3" in out
    assert "c2=''[颜色标注:高亮=red]" in out


def test_resolve_llm_parse_chunk_body_legacy_unchanged() -> None:
    chunk = "plain | a\nplain | b"
    assert resolve_llm_parse_chunk_body(chunk, table_only=True) == chunk
    assert resolve_llm_parse_chunk_body(chunk, table_only=False) == chunk


def test_resolve_llm_parse_chunk_body_table_only_and_strip_cols() -> None:
    chunk = "\n".join(
        [
            "[处理单元 heading_path=x]",
            "prelude",
            SAMPLE_TABLE_WITH_EMPTY_C4,
        ]
    )
    out = resolve_llm_parse_chunk_body(
        chunk,
        table_only=True,
        strip_trailing_empty_cols=True,
    )
    assert out.startswith("[DOCX_V2_TABLE")
    assert "处理单元" not in out
    assert "prelude" not in out
    assert "cols=4" in out
    assert "c4=" not in out


def test_resolve_llm_parse_chunk_body_can_disable_strip() -> None:
    chunk = "[处理单元 heading_path=x]\n" + SAMPLE_TABLE_WITH_EMPTY_C4
    out = resolve_llm_parse_chunk_body(
        chunk,
        table_only=True,
        strip_trailing_empty_cols=False,
    )
    assert "cols=5" in out
    assert "c4=''" in out


def test_filter_renumbers_work_idx() -> None:
    chunks = ["plain", "[DOCX_V2_TABLE idx=1]\nr1:\tx", "also plain"]
    items = filter_table_work_items(chunks, parse_route="docx_v2")
    assert len(items) == 1
    assert items[0][0] == 1
    assert "DOCX_V2_TABLE" in items[0][1]
