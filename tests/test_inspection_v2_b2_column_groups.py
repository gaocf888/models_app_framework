from __future__ import annotations

from app.inspection_v2.docx_v2_table_parse import (
    build_index_pairs,
    parse_column_groups,
    parse_table_rows,
)
from app.inspection_v2.tube_thickness_bind_guard import apply_docx_v2_tube_thickness_bind_guard

CHUNK_B2 = """
[DOCX_V2_TABLE idx=1 rows=21 cols=8]
r0: c0..c7='水冷壁左墙B2贴壁风孔'[重复表题×8]
r1: c0-c7='B2贴壁风（下数：6、7、8、9、11、13、14、15、17、18、19、21、22、23、24减薄长度约2.8m）'[hmerge×8]
r2: c0-c3='上'[hmerge×4] | c4-c7='下'[hmerge×4]
r3: c0='编号' | c1='测量值' | c2='编号' | c3='测量值' | c4='编号' | c5='测量值' | c6='编号' | c7='测量值'
r4: c0='2' | c1='6.9' | c2='' | c3='' | c4='2' | c5='6.9' | c6='20' | c7='5.2'
r5: c0='4' | c1='6.5' | c2='' | c3='' | c4='4' | c5='5.6' | c6='21' | c7='5.1'
r6: c0='6' | c1='6.4' | c2='' | c3='' | c4='5' | c5='5.7' | c6='22' | c7='5.0'
"""


def _lines() -> list[str]:
    return [ln for ln in CHUNK_B2.strip().splitlines() if ln.strip()]


def test_b2_column_groups_split_wide_up_down_spans() -> None:
    groups = parse_column_groups(_lines())
    assert [(g.direction, g.idx_col, g.thk_col) for g in groups] == [
        ("上", 0, 1),
        ("上", 2, 3),
        ("下", 4, 5),
        ("下", 6, 7),
    ]


def test_b2_index_pairs_include_c6_c7_newspaper_column() -> None:
    lines = _lines()
    cells = parse_table_rows(lines)
    pairs = build_index_pairs(lines, cells)
    r5_c67 = next(
        p for p in pairs if p.row_ri == 5 and p.idx_col == 6 and p.direction == "下"
    )
    assert r5_c67.index_val == 21
    assert r5_c67.thickness == 5.1

    r5_c01 = next(
        p for p in pairs if p.row_ri == 5 and p.idx_col == 0 and p.direction == "上"
    )
    assert r5_c01.index_val == 4
    assert r5_c01.thickness == 6.5


def test_b2_bind_guard_keeps_correct_llm_tube_21() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙B2贴壁风孔",
            "行号": "1",
            "管号": "21",
            "壁厚": 5.1,
            "检测类型": "测厚",
        }
    ]
    out = apply_docx_v2_tube_thickness_bind_guard(records, CHUNK_B2)
    assert out[0]["管号"] == "21"
    assert not any("bind_guard" in str(w) for w in out[0].get("warnings") or [])


def test_b2_bind_guard_keeps_correct_upper_negative_tube() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙B2贴壁风孔",
            "行号": "1",
            "管号": "-4",
            "壁厚": 6.5,
            "检测类型": "测厚",
        }
    ]
    out = apply_docx_v2_tube_thickness_bind_guard(records, CHUNK_B2)
    assert out[0]["管号"] == "-4"
