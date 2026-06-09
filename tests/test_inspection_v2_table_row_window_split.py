from __future__ import annotations

from app.inspection_v2.processing_units import split_docx_v2_by_processing_units
from app.inspection_v2.table_row_window_split import (
    detect_table_data_start,
    split_table_lines_by_row_windows,
)

# 低再 8 列组合编号表（与现网日志样例结构一致，14 行）
SAMPLE_TABLE = """
[DOCX_V2_TABLE idx=6 rows=14 cols=8]
r0: c0..c7='低温再热器第二层L14吹灰器上水平管'[重复表题×8]
r1: c0='编号' | c1='测量值' | c2='编号' | c3='测量值' | c4='编号' | c5='测量值' | c6='编号' | c7='测量值'
r2: c0='1-1' | c1='4.31' | c2='33-1' | c3='3.79' | c4='1-2' | c5='4.10' | c6='33-2' | c7='3.80'
r3: c0='1-2' | c1='4.20' | c2='33-2' | c3='3.70' | c4='1-3' | c5='4.00' | c6='33-3' | c7='3.60'
r4: c0='2-1' | c1='4.15' | c2='34-1' | c3='3.65' | c4='2-2' | c5='3.95' | c6='34-2' | c7='3.55'
r5: c0='2-2' | c1='4.05' | c2='34-2' | c3='3.60' | c4='2-3' | c5='3.90' | c6='34-3' | c7='3.50'
r6: c0='3-1' | c1='2.85'[颜色标注:高亮=red] | c2='35-1' | c3='3.50' | c4='3-2' | c5='3.85' | c6='35-2' | c7='3.45'
r7: c0='3-2' | c1='3.80' | c2='35-2' | c3='3.40' | c4='3-3' | c5='3.75' | c6='35-3' | c7='3.40'
r8: c0='4-1' | c1='3.70' | c2='36-1' | c3='3.35' | c4='4-2' | c5='3.65' | c6='36-2' | c7='3.35'
r9: c0='4-2' | c1='3.60' | c2='36-2' | c3='3.30' | c4='4-3' | c5='3.60' | c6='36-3' | c7='3.30'
r10: c0='5-1' | c1='3.55' | c2='37-1' | c3='3.25' | c4='5-2' | c5='3.55' | c6='37-2' | c7='3.25'
r11: c0='5-2' | c1='3.50' | c2='37-2' | c3='3.20' | c4='5-3' | c5='3.50' | c6='37-3' | c7='3.20'
r12: c0='6-1' | c1='3.45' | c2='38-1' | c3='3.15' | c4='6-2' | c5='3.45' | c6='38-2' | c7='3.15'
r13: c0='6-2' | c1='3.40' | c2='38-2' | c3='3.12'[颜色标注:高亮=red] | c4='6-3' | c5='3.40' | c6='38-3' | c7='3.12'[颜色标注:高亮=red]
""".strip()


def _table_only_chunks(chunks: list[str]) -> list[str]:
    return [c for c in chunks if "[DOCX_V2_TABLE" in c]


def test_detect_data_start_after_index_header_row() -> None:
    lines = [ln for ln in SAMPLE_TABLE.splitlines() if ln.strip()]
    assert detect_table_data_start(lines) == 2


def test_split_table_by_row_windows_keeps_header_in_each_part() -> None:
    lines = [ln for ln in SAMPLE_TABLE.splitlines() if ln.strip()]
    parts = split_table_lines_by_row_windows(
        lines,
        max_table_chars=800,
        data_rows_per_window=4,
    )
    assert len(parts) >= 2
    for part in parts:
        body = "\n".join(part)
        assert "r0:" in body
        assert "编号" in body and "测量值" in body
        assert " sub=" in body


def test_split_docx_v2_with_heading_and_prelude() -> None:
    text = "\n".join(
        [
            "（一）炉膛水冷壁检查情况",
            "低再第二层L/R14吹灰器通道上水平管测厚数据（mm）低于3.15mm超标，共超标21根",
            SAMPLE_TABLE,
        ]
    )
    chunks = split_docx_v2_by_processing_units(
        text,
        max_chunk_chars=900,
        table_data_rows_per_window=4,
    )
    table_chunks = _table_only_chunks(chunks)
    assert len(table_chunks) >= 2
    assert table_chunks[0].count("低再第二层") == 1
    assert all("处理单元 heading_path=" in c for c in table_chunks)
    assert all("r0:" in c and "r1:" in c for c in table_chunks)


def _build_wide_sample_table(n_data_rows: int) -> list[str]:
    """8 列组合编号表，用于回归「多窗但每窗未超长时不应压到 1 行/窗」。"""
    lines = [
        "[DOCX_V2_TABLE idx=6 rows=99 cols=8]",
        "r0: c0..c7='低温再热器第二层L14吹灰器上水平管'[重复表题×8]",
        "r1: c0='编号' | c1='测量值' | c2='编号' | c3='测量值' | c4='编号' | c5='测量值' | c6='编号' | c7='测量值'",
    ]
    for i in range(n_data_rows):
        ri = i + 2
        n = i + 1
        lines.append(
            f"r{ri}: c0='{n}-1' | c1='4.31' | c2='{n + 32}-1' | c3='3.79' | "
            f"c4='{n}-2' | c5='4.10' | c6='{n + 32}-2' | c7='3.80'"
        )
    return lines


def test_row_window_not_collapsed_when_multiple_windows_fit_budget() -> None:
    """52 行数据 + rows_per_window=20：应约 3 窗，不应缩成 52 窗各 1 行。"""
    lines = _build_wide_sample_table(52)
    parts = split_table_lines_by_row_windows(
        lines,
        max_table_chars=3000,
        data_rows_per_window=20,
    )
    assert len(parts) == 3
    assert " data=r2-r21" in "\n".join(parts[0])
    assert " data=r42-r" in "\n".join(parts[2]) or "r53:" in "\n".join(parts[2])


def test_row_window_still_shrinks_when_single_window_exceeds_budget() -> None:
    lines = _build_wide_sample_table(25)
    parts = split_table_lines_by_row_windows(
        lines,
        max_table_chars=600,
        data_rows_per_window=20,
    )
    assert len(parts) > 3
    for part in parts:
        body = "\n".join(part)
        assert "r0:" in body and "编号" in body
