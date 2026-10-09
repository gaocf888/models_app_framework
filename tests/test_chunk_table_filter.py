from app.inspection_v2.chunk_table_filter import (
    chunk_contains_table,
    chunk_matches_thickness_structure_whitelist,
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


def test_extract_docx_v2_table_blocks_keeps_heading_and_prelude() -> None:
    chunk = "\n".join(
        [
            "[处理单元 heading_path=（一）炉膛水冷壁检查情况]",
            "冷灰斗后墙下弯头规格Φ38×7.3mm材质15CrMoG\t测厚记录",
            "[DOCX_V2_TABLE idx=1 rows=2 cols=2]",
            "r0: c0='管子编号' | c1='壁厚'",
            "r1: c0='A3下2' | c1='7.4'",
            "表后无关说明应丢弃",
        ]
    )
    out = extract_docx_v2_table_blocks_for_llm(chunk)
    assert "[处理单元 heading_path=" in out
    assert "冷灰斗后墙下弯头" in out
    assert out.startswith("[处理单元") or "处理单元" in out.split("[DOCX_V2_TABLE")[0]
    assert "[DOCX_V2_TABLE idx=1" in out
    assert "r0:" in out and "r1:" in out
    assert "表后无关说明" not in out


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


def test_resolve_llm_parse_chunk_body_table_only_keeps_prelude_and_strip_cols() -> None:
    chunk = "\n".join(
        [
            "[处理单元 heading_path=x]",
            "水冷壁B层吹灰器测厚以吹灰器为中心",
            SAMPLE_TABLE_WITH_EMPTY_C4,
        ]
    )
    out = resolve_llm_parse_chunk_body(
        chunk,
        table_only=True,
        strip_trailing_empty_cols=True,
    )
    assert "[处理单元 heading_path=x]" in out
    assert "水冷壁B层吹灰器" in out
    assert "[DOCX_V2_TABLE" in out
    assert "cols=4" in out
    assert "c4=" not in out


def test_resolve_llm_parse_chunk_body_can_disable_strip() -> None:
    chunk = "[处理单元 heading_path=x]\nprelude\n" + SAMPLE_TABLE_WITH_EMPTY_C4
    out = resolve_llm_parse_chunk_body(
        chunk,
        table_only=True,
        strip_trailing_empty_cols=False,
    )
    assert "prelude" in out
    assert "cols=5" in out
    assert "c4=''" in out


def test_filter_renumbers_work_idx() -> None:
    chunks = ["plain", "[DOCX_V2_TABLE idx=1]\nr1:\tx", "also plain"]
    items = filter_table_work_items(chunks, parse_route="docx_v2")
    assert len(items) == 1
    assert items[0][0] == 1
    assert "DOCX_V2_TABLE" in items[0][1]


def test_structure_whitelist_matches_no_header_up_down_pairs() -> None:
    """图1类：无编号/壁厚列名，仅有上/下 + 整数|小数对。"""
    chunk = "\n".join(
        [
            "[处理单元 heading_path=x]",
            SAMPLE_TABLE_WITH_EMPTY_C4,
        ]
    )
    assert chunk_matches_thickness_structure_whitelist(chunk) is True


def test_structure_whitelist_matches_idx_thk_headers() -> None:
    chunk = "\n".join(
        [
            "[DOCX_V2_TABLE idx=1 rows=2 cols=4]",
            "r0: c0='编号' | c1='测量值' | c2='编号' | c3='测量值'",
            "r1: c0='2' | c1='6.9' | c2='3' | c3='5.0'",
        ]
    )
    assert chunk_matches_thickness_structure_whitelist(chunk) is True


def test_structure_whitelist_matches_tube_remaining_thk_headers() -> None:
    chunk = "\n".join(
        [
            "[DOCX_V2_TABLE idx=1 rows=2 cols=2]",
            "r0: c0='管子编号' | c1='剩余壁厚（mm）'",
            "r1: c0='10' | c1='6.56'",
        ]
    )
    assert chunk_matches_thickness_structure_whitelist(chunk) is True


def test_structure_whitelist_matches_omit_prefix_pair() -> None:
    chunk = "\n".join(
        [
            "[DOCX_V2_TABLE idx=1 rows=2 cols=2]",
            "r0: c0='A8下2' | c1='7.4'",
            "r1: c0='3' | c1='7.2'",
        ]
    )
    assert chunk_matches_thickness_structure_whitelist(chunk) is True


def test_structure_whitelist_rejects_non_thickness_table() -> None:
    chunk = "\n".join(
        [
            "[DOCX_V2_TABLE idx=1 rows=3 cols=2]",
            "r0: c0='姓名' | c1='职务'",
            "r1: c0='张三' | c1='班长'",
            "r2: c0='李四' | c1='技术员'",
        ]
    )
    assert chunk_matches_thickness_structure_whitelist(chunk) is False


def test_filter_structure_whitelist_drops_non_thickness() -> None:
    good = "\n".join(
        [
            "[DOCX_V2_TABLE idx=1 rows=2 cols=2]",
            "r0: c0='管子编号' | c1='剩余壁厚（mm）'",
            "r1: c0='1' | c1='6.56'",
        ]
    )
    bad = "\n".join(
        [
            "[DOCX_V2_TABLE idx=2 rows=2 cols=2]",
            "r0: c0='姓名' | c1='职务'",
            "r1: c0='张三' | c1='班长'",
        ]
    )
    items_off = filter_table_work_items([good, bad], parse_route="docx_v2", structure_whitelist=False)
    assert len(items_off) == 2
    items_on = filter_table_work_items([good, bad], parse_route="docx_v2", structure_whitelist=True)
    assert len(items_on) == 1
    assert items_on[0][0] == 1
    assert "管子编号" in items_on[0][1]
