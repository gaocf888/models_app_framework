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
