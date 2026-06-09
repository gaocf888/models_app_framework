from __future__ import annotations

from app.inspection_v2.docx_v2_table_parse import (
    DIRECTION_SOURCE_DEFAULT_DOWN,
    DIRECTION_SOURCE_EXPLICIT,
    DIRECTION_SOURCE_LOCATION_SKIP,
    _build_direction_groups_for_band,
    build_index_pairs,
    parse_table_rows,
)
from app.inspection_v2.tube_direction_sign_guard import apply_docx_v2_tube_direction_sign_guard

CHUNK_B2 = """
[DOCX_V2_TABLE idx=1 rows=21 cols=8]
r0: c0..c7='水冷壁左墙B2贴壁风孔'[重复表题×8]
r1: c0-c7='B2贴壁风（下数：6、7、8、9、11、13、14、15、17、18、19、21、22、23、24减薄长度约2.8m）'[hmerge×8]
r2: c0-c3='上'[hmerge×4] | c4-c7='下'[hmerge×4]
r3: c0='编号' | c1='测量值' | c2='编号' | c3='测量值' | c4='编号' | c5='测量值' | c6='编号' | c7='测量值'
r4: c0='2' | c1='6.9' | c2='' | c3='' | c4='2' | c5='6.9' | c6='20' | c7='5.2'
r5: c0='4' | c1='6.5' | c2='' | c3='' | c4='4' | c5='5.6' | c6='21' | c7='5.1'
"""

CHUNK_XIASHU_NARROW = """
[DOCX_V2_TABLE idx=1 rows=4 cols=2]
r0: c0-c1='下数'[hmerge×2]
r1: c0='23' | c1='7.2'
r2: c0='25' | c1='7.0'
"""

CHUNK_LOCATION_ONLY = """
[DOCX_V2_TABLE idx=1 rows=3 cols=2]
r0: c0..c1='水冷壁右墙第三层右2下1.5米处'[重复表题×2]
r1: c0='23' | c1='7.2'
r2: c0='25' | c1='7.0'
"""


def _lines(chunk: str) -> list[str]:
    return [ln for ln in chunk.strip().splitlines() if ln.strip()]


def test_b2_direction_source_explicit_nearest() -> None:
    lines = _lines(CHUNK_B2)
    cells = parse_table_rows(lines)
    groups, data_start, source = _build_direction_groups_for_band(lines, cells, 0, 5)
    assert source == DIRECTION_SOURCE_EXPLICIT
    assert data_start == 4
    assert ("上", 0, 1) in [(g.direction, g.idx_col, g.thk_col) for g in groups]
    assert ("下", 6, 7) in [(g.direction, g.idx_col, g.thk_col) for g in groups]


def test_xiashu_narrow_explicit_direction() -> None:
    lines = _lines(CHUNK_XIASHU_NARROW)
    cells = parse_table_rows(lines)
    _, _, source = _build_direction_groups_for_band(lines, cells, 0, 2)
    assert source == DIRECTION_SOURCE_EXPLICIT
    pairs = build_index_pairs(lines, cells)
    assert pairs and pairs[0].direction == "下"


def test_location_only_repeat_title_skips_sign_source() -> None:
    lines = _lines(CHUNK_LOCATION_ONLY)
    cells = parse_table_rows(lines)
    _, _, source = _build_direction_groups_for_band(lines, cells, 0, 2)
    assert source == DIRECTION_SOURCE_LOCATION_SKIP


def test_sign_guard_upper_negative_on_b2() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙B2贴壁风孔",
            "行号": "1",
            "管号": "4",
            "壁厚": 6.5,
        }
    ]
    out = apply_docx_v2_tube_direction_sign_guard(records, CHUNK_B2)
    assert out[0]["管号"] == "-4"


def test_sign_guard_keeps_correct_upper_negative() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙B2贴壁风孔",
            "行号": "1",
            "管号": "-4",
            "壁厚": 6.5,
        }
    ]
    out = apply_docx_v2_tube_direction_sign_guard(records, CHUNK_B2)
    assert out[0]["管号"] == "-4"
    assert not any("direction_sign_guard:" in str(w) for w in out[0].get("warnings") or [])


def test_sign_guard_xiashu_strips_wrong_negative() -> None:
    records = [{"检测位置": "水冷壁右墙", "行号": "1", "管号": "-23", "壁厚": 7.2}]
    out = apply_docx_v2_tube_direction_sign_guard(records, CHUNK_XIASHU_NARROW)
    assert out[0]["管号"] == "23"


def test_sign_guard_location_only_does_not_change_tube() -> None:
    records = [{"检测位置": "水冷壁右墙第三层", "行号": "1", "管号": "-23", "壁厚": 7.2}]
    out = apply_docx_v2_tube_direction_sign_guard(records, CHUNK_LOCATION_ONLY)
    assert out[0]["管号"] == "-23"


def test_default_down_header_only_strip_negative() -> None:
    chunk = """
[DOCX_V2_TABLE idx=1 rows=4 cols=2]
r0: c0='编号' | c1='测量值'
r1: c0='5' | c1='7.0'
"""
    lines = _lines(chunk)
    cells = parse_table_rows(lines)
    _, _, source = _build_direction_groups_for_band(lines, cells, 0, 1)
    assert source == DIRECTION_SOURCE_DEFAULT_DOWN
    records = [{"检测位置": "水冷壁右墙", "行号": "1", "管号": "-5", "壁厚": 7.0}]
    out = apply_docx_v2_tube_direction_sign_guard(records, chunk)
    assert out[0]["管号"] == "5"
    records2 = [{"检测位置": "水冷壁右墙", "行号": "1", "管号": "5", "壁厚": 7.0}]
    out2 = apply_docx_v2_tube_direction_sign_guard(records2, chunk)
    assert out2[0]["管号"] == "5"
