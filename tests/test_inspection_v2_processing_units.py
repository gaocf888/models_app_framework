from __future__ import annotations

from app.inspection_v2.processing_units import (
    segment_docx_v2_by_headings,
    split_docx_v2_by_processing_units,
)


def test_segment_by_headings_preface_and_sections() -> None:
    lines = [
        "说明前言一段",
        "（一）炉膛区域",
        "表格前说明",
        "[DOCX_V2_TABLE idx=1 rows=1 cols=1]",
        "r0: c0='a'",
        "（二）尾部区域",
        "尾部正文",
    ]
    units = segment_docx_v2_by_headings(lines)
    assert len(units) == 3
    assert units[0][0] == "前言"
    assert "说明前言" in "\n".join(units[0][1])
    assert units[1][0] == "（一）炉膛区域"
    assert any("DOCX_V2_TABLE" in x for x in units[1][1])
    assert units[2][0] == "（二）尾部区域"


def test_split_docx_v2_table_precedes_with_text_above_only_in_table_chunk() -> None:
    """表上方正文与整张表同块；两个表则两块。"""
    text = "\n".join(
        [
            "（一）段",
            "表前说明A",
            "[DOCX_V2_TABLE idx=1 rows=1 cols=1]",
            "r0: c0='a'",
            "间隔说明B",
            "[DOCX_V2_TABLE idx=2 rows=1 cols=1]",
            "r0: c0='b'",
        ]
    )
    chunks = split_docx_v2_by_processing_units(text, max_chunk_chars=8000)
    assert len(chunks) == 2
    assert "表前说明A" in chunks[0] and "[DOCX_V2_TABLE idx=1" in chunks[0] and "r0: c0='a'" in chunks[0]
    assert "间隔说明B" in chunks[1] and "[DOCX_V2_TABLE idx=2" in chunks[1]


def test_split_docx_v2_trailing_text_splits_by_size() -> None:
    """最后一个表之后的纯文本按大小切开多块。"""
    long_tail = "尾段" * 400
    text = "\n".join(
        [
            "（一）段",
            "[DOCX_V2_TABLE idx=1 rows=1 cols=1]",
            "r0: c0='x'",
            long_tail,
        ]
    )
    chunks = split_docx_v2_by_processing_units(text, max_chunk_chars=500)
    assert len(chunks) >= 2
    assert "[DOCX_V2_TABLE" in chunks[0]
    assert all("处理单元 heading_path=" in c for c in chunks)


def test_split_docx_v2_includes_heading_path_header() -> None:
    text = "\n".join(
        [
            "（一）测试段",
            "[DOCX_V2_TABLE idx=1 rows=1 cols=2]",
            "r0: c0='x' | c1='1'",
        ]
    )
    chunks = split_docx_v2_by_processing_units(text, max_chunk_chars=8000)
    assert len(chunks) >= 1
    assert "处理单元 heading_path=" in chunks[0]
    assert "（一）测试段" in chunks[0]
    assert "[DOCX_V2_TABLE" in chunks[0]
