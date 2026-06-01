from __future__ import annotations

from app.inspection_v2.docx_v2_table_parse import build_index_pairs, parse_table_rows
from app.inspection_v2.tube_thickness_bind_guard import apply_docx_v2_tube_thickness_bind_guard

CHUNK_8COL = """
[DOCX_V2_TABLE idx=4 rows=16 cols=8]
r0: c0-c3='水冷壁右墙第1层第1贴壁风孔'[hmerge×4] | c4-c7='水冷壁右墙第1层第2贴壁风孔'[hmerge×4]
r1: c0-c3='右墙1-1'[hmerge×4] | c4-c7='右墙1-2'[hmerge×4]
r2: c0-c1='上'[hmerge×2] | c2-c3='下'[hmerge×2] | c4-c5='上'[hmerge×2] | c6-c7='下'[hmerge×2]
r8: c0-c3='水冷壁左墙第1层第1贴壁风孔'[hmerge×4] | c4-c7='水冷壁左墙第1层第2贴壁风孔'[hmerge×4]
r9: c0-c3='左墙1-1'[hmerge×4] | c4-c7='左墙1-2'[hmerge×4]
r10: c0-c1='上'[hmerge×2] | c2-c3='下'[hmerge×2] | c4-c5='上'[hmerge×2] | c6-c7='下'[hmerge×2]
r11: c0='1' | c1='7.4' | c2='2' | c3='7.1' | c4='1' | c5='7.2' | c6='2' | c7='7.1'
r12: c0='2' | c1='7.2' | c2='3' | c3='7.2' | c4='2' | c5='7.4' | c6='3' | c7='7.0'
"""


def test_build_index_pairs_left_wall_r12() -> None:
    lines = [ln for ln in CHUNK_8COL.strip().splitlines() if ln.strip()]
    cells = parse_table_rows(lines)
    pairs = build_index_pairs(lines, cells)
    left_r12_up = next(
        p
        for p in pairs
        if p.row_ri == 12
        and p.idx_col == 0
        and p.direction == "上"
        and "左墙" in p.scope_label
    )
    left_r12_down = next(
        p
        for p in pairs
        if p.row_ri == 12
        and p.idx_col == 2
        and p.direction == "下"
    )
    assert left_r12_up.index_val == 2
    assert left_r12_up.thickness == 7.2
    assert left_r12_down.index_val == 3
    assert left_r12_down.thickness == 7.2


def test_bind_guard_fixes_wrong_upper_tube() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙第1层第1贴壁风孔",
            "行号": "1",
            "管号": "-3",
            "壁厚": 7.2,
            "检测类型": "测厚",
        }
    ]
    out = apply_docx_v2_tube_thickness_bind_guard(records, CHUNK_8COL)
    assert out[0]["管号"] == "-2"
    assert any("bind_guard" in str(w) for w in out[0].get("warnings") or [])


def test_bind_guard_keeps_correct_lower_tube() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙第1层第1贴壁风孔",
            "行号": "1",
            "管号": "3",
            "壁厚": 7.2,
            "检测类型": "测厚",
        }
    ]
    out = apply_docx_v2_tube_thickness_bind_guard(records, CHUNK_8COL)
    assert out[0]["管号"] == "3"


def test_bind_guard_fixes_positive_upper_to_negative() -> None:
    """LLM 输出上侧 2 但未加负号，且与同壁厚下侧 3 并存时，应绑到上侧 2。"""
    records = [
        {
            "检测位置": "水冷壁左墙第1层第1贴壁风孔",
            "行号": "1",
            "管号": "2",
            "壁厚": 7.2,
            "检测类型": "测厚",
        }
    ]
    out = apply_docx_v2_tube_thickness_bind_guard(records, CHUNK_8COL)
    assert out[0]["管号"] == "-2"
