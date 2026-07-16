from __future__ import annotations

from app.inspection_v2.combo_index_guard import apply_docx_v2_combo_index_guard
from app.inspection_v2.docx_v2_table_parse import build_combo_cells, parse_table_rows
from app.inspection_v2.record_normalization import (
    COMBO_INDEX_FROM_CHUNK,
    apply_deterministic_rules_to_record,
)
from app.inspection_v2.tube_thickness_bind_guard import apply_docx_v2_tube_thickness_bind_guard

CHUNK_COMBO = """
[DOCX_V2_TABLE idx=1 rows=6 cols=4]
r0: c0-c3='水冷壁左墙第1层第1贴壁风孔'[hmerge×4]
r1: c0-c1='上'[hmerge×2] | c2-c3='下'[hmerge×2]
r2: c0='2-1' | c1='7.5' | c2='3-2' | c3='6.8'
"""


def test_build_combo_cells_from_chunk() -> None:
    lines = [ln for ln in CHUNK_COMBO.strip().splitlines() if ln.strip()]
    cells = parse_table_rows(lines)
    combo = build_combo_cells(lines, cells)
    up = next(c for c in combo if c.raw == "2-1")
    assert up.row_part == "2"
    assert up.tube_part == "1"
    assert up.thickness == 7.5
    assert "左墙" in up.scope_label


def test_combo_guard_marks_split_record_on_wall() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙第1层第1贴壁风孔",
            "行号": "2",
            "管号": "1",
            "壁厚": 7.5,
            "检测类型": "测厚",
        }
    ]
    out = apply_docx_v2_combo_index_guard(records, CHUNK_COMBO)
    assert out[0][COMBO_INDEX_FROM_CHUNK] is True
    assert out[0]["行号"] == "2"
    assert out[0]["管号"] == "1"
    assert any(str(w).startswith("combo_index_guard:") for w in out[0].get("warnings") or [])


def test_combo_guard_then_normalization_skips_wall_row_one() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙第1层第1贴壁风孔",
            "行号": "2",
            "管号": "1",
            "壁厚": 7.5,
        }
    ]
    guarded = apply_docx_v2_combo_index_guard(records, CHUNK_COMBO)
    norm = apply_deterministic_rules_to_record(guarded[0])
    assert norm["行号"] == "2"
    assert norm["管号"] == "1"
    assert norm.get(COMBO_INDEX_FROM_CHUNK) is True
    assert not any("row_fix" in str(w) for w in norm.get("warnings") or [])


def test_combo_guard_literal_in_row_field() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙第1层第1贴壁风孔",
            "行号": "2-1",
            "管号": "1",
            "壁厚": 7.5,
        }
    ]
    out = apply_docx_v2_combo_index_guard(records, CHUNK_COMBO)
    assert out[0][COMBO_INDEX_FROM_CHUNK] is True
    assert out[0]["行号"] == "2"
    assert out[0]["管号"] == "1"


def test_combo_guard_skips_bind_guard() -> None:
    records = [
        {
            "检测位置": "水冷壁左墙第1层第1贴壁风孔",
            "行号": "2",
            "管号": "1",
            "壁厚": 7.5,
            COMBO_INDEX_FROM_CHUNK: True,
            "warnings": ["combo_index_guard:chunk_cell=2-1(r2 c0)"],
        }
    ]
    out = apply_docx_v2_tube_thickness_bind_guard(records, CHUNK_COMBO)
    assert out[0]["管号"] == "1"
    assert not any(str(w).startswith("bind_guard:") for w in out[0].get("warnings") or [])


def test_without_chunk_flag_wall_rules_still_apply() -> None:
    """无 chunk 标记时，水冷壁 行号=2 管号=1 仍走设备语义规则。"""
    out = apply_deterministic_rules_to_record(
        {
            "检测位置": "水冷壁左墙第1层第1贴壁风孔",
            "行号": "2",
            "管号": "1",
            "壁厚": 7.5,
        }
    )
    assert out["行号"] == "1"
    assert out["管号"] == "2"


CHUNK_ECONOMIZER_COMBO = """
[DOCX_V2_TABLE idx=1 rows=4 cols=4]
r0: c0='管子编号' | c1='壁厚 mm' | c2='管子编号' | c3='壁厚 mm'
r1: c0='4-2' | c1='6.69' | c2='127-2' | c3='6.98'
r2: c0='17-2' | c1='7.11' | c2='130-2' | c3='6.69'
"""


def test_economizer_管子编号_builds_combo_cells() -> None:
    lines = [ln for ln in CHUNK_ECONOMIZER_COMBO.strip().splitlines() if ln.strip()]
    cells = parse_table_rows(lines)
    combo = build_combo_cells(lines, cells)
    assert len(combo) >= 4
    c42 = next(c for c in combo if c.raw == "4-2")
    assert c42.row_part == "4"
    assert c42.tube_part == "2"
    assert c42.thickness == 6.69


def test_economizer_combo_guard_protects_split_tube() -> None:
    """省煤器 + 已拆分组合：guard 打标后设备规则不得把管号改成 1。"""
    records = [
        {
            "检测位置": "省煤器上部区域吹灰器01/02通道下方",
            "行号": "4",
            "管号": "2",
            "壁厚": 6.69,
            "检测类型": "测厚",
        }
    ]
    guarded = apply_docx_v2_combo_index_guard(records, CHUNK_ECONOMIZER_COMBO)
    assert guarded[0][COMBO_INDEX_FROM_CHUNK] is True
    assert guarded[0]["行号"] == "4"
    assert guarded[0]["管号"] == "2"
    norm = apply_deterministic_rules_to_record(guarded[0])
    assert norm["行号"] == "4"
    assert norm["管号"] == "2"
    assert not any("tube_fix" in str(w) for w in norm.get("warnings") or [])


def test_economizer_split_combo_skips_tube_fix_without_guard() -> None:
    """方案 B：无 guard 标记时，已拆分组合（管号≠±1）仍跳过省煤器管号→1。"""
    out = apply_deterministic_rules_to_record(
        {
            "检测位置": "省煤器上部区域吹灰器01/02通道下方",
            "行号": "4",
            "管号": "2",
            "壁厚": 6.69,
        }
    )
    assert out["行号"] == "4"
    assert out["管号"] == "2"
    assert any("已拆分组合" in str(w) for w in out.get("warnings") or [])
    assert not any("tube_fix" in str(w) for w in out.get("warnings") or [])


def test_economizer_non_combo_still_forces_tube_one() -> None:
    """省煤器非组合：管号已是 1 保持；编号误写在管号且行号=1 时仍迁移。"""
    out2 = apply_deterministic_rules_to_record(
        {
            "检测位置": "省煤器上部",
            "行号": "10",
            "管号": "1",
            "壁厚": 6.5,
        }
    )
    assert out2["行号"] == "10"
    assert out2["管号"] == "1"
    # LLM 把编号只放在管号、行号为 1：仍应迁到行号并管号→1
    out3 = apply_deterministic_rules_to_record(
        {
            "检测位置": "省煤器上部",
            "行号": "1",
            "管号": "17",
            "壁厚": 6.5,
        }
    )
    assert out3["行号"] == "17"
    assert out3["管号"] == "1"
