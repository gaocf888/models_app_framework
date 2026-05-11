from __future__ import annotations

from pathlib import Path
from typing import Iterable

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


def normalize_shading_fill(fill: str | None) -> str | None:
    """将 Word `w:shd/@w:fill` 规范为 6 位大写 hex（无 #）。"""
    if fill is None:
        return None
    raw = str(fill).strip()
    if not raw or raw.lower() == "auto":
        return None
    s = raw.upper().replace("#", "")
    if len(s) == 8 and s.startswith("FF"):
        s = s[2:]
    if len(s) >= 6:
        return s[-6:]
    return s if s else None


def _cell_shd_fill(cell) -> str | None:
    tc = cell._tc
    tc_pr = tc.find(qn("w:tcPr"))
    if tc_pr is None:
        return None
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        return None
    fill = shd.get(qn("w:fill"))
    return normalize_shading_fill(fill)


def _is_candidate_shading(
    fill_norm: str | None,
    candidate_fills: set[str],
) -> bool:
    if not fill_norm:
        return False
    # 纯白底纹不作为候选（仍可通过配置显式列入命中表）
    if fill_norm == "FFFFFF":
        return False
    return fill_norm in candidate_fills


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if not it or it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def _normalize_font_color_hex(rgb_str: str) -> str | None:
    """将 run 字体色规范为 6 位大写 hex；无法解析或 AUTO 返回 None。"""
    s = rgb_str.upper().replace("#", "").strip()
    if not s or s == "AUTO":
        return None
    if len(s) == 8 and s.startswith("FF"):
        s = s[2:]
    if len(s) >= 6:
        return s[-6:]
    return s if s else None


def _is_default_black_font(hex6: str | None) -> bool:
    """默认黑色（常见 Word 显式 000000）不视为需输出的检修样式。"""
    return hex6 is None or hex6 == "000000"


def _collect_cell_run_color_marks(cell) -> list[str]:
    """收集单元格内 run 级颜色信息：字体色（非默认黑）/高亮/文本底纹。"""
    marks: list[str] = []
    for p in cell.paragraphs:
        for run in p.runs:
            # 1) 字体颜色（RGB）：默认黑色 000000 不输出，避免海量重复 token
            try:
                rgb = run.font.color.rgb if run.font and run.font.color else None
            except Exception:  # noqa: BLE001
                rgb = None
            if rgb is not None:
                val = _normalize_font_color_hex(str(rgb))
                if val and not _is_default_black_font(val):
                    marks.append(f"字体={val}")

            r_pr = run._r.find(qn("w:rPr"))  # noqa: SLF001
            if r_pr is None:
                continue
            # 2) 高亮
            hl = r_pr.find(qn("w:highlight"))
            if hl is not None:
                hv = (hl.get(qn("w:val")) or "").strip()
                if hv and hv.lower() not in {"none", "default"}:
                    marks.append(f"高亮={hv}")
            # 3) run 级文本底纹
            shd = r_pr.find(qn("w:shd"))
            if shd is not None:
                fill = normalize_shading_fill(shd.get(qn("w:fill")))
                if fill and fill != "FFFFFF":
                    marks.append(f"文本底纹={fill}")
    return _dedupe_keep_order(marks)


def _iter_block_items(document: Document) -> Iterable[Paragraph | Table]:
    body = document.element.body
    for child in body:
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _escape_cell_text(s: str) -> str:
    t = s.replace("\r", " ").replace("\n", " ")
    return t.replace("'", "''")


def serialize_docx_for_inspection_v2(
    path: str | Path,
    *,
    candidate_fills: set[str],
) -> str:
    """
    将 docx 按文档流展开为供 LLM 使用的文本：段落原样，表格按行列输出。
    颜色标注策略（默认不写、异常才写，节省 token）：
    - 命中候选底纹色时附加「超标候选」；
    - 单元格非白底纹：若非候选底纹（仍属异常底色），在「颜色标注」中输出 ``底纹=``；
      若为候选底纹，仅以「超标候选」表达，不在「颜色标注」内重复 ``底纹=``；
    - run 级：高亮（任意非 default/none）、非白文本底纹、**非默认黑色**字体色（000000 省略）；
    - 仅当上述片段非空时附加对应 ``[颜色标注:...]`` 块。
    """
    path = Path(path)
    doc = Document(str(path))
    out: list[str] = []
    table_idx = 0
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            t = (block.text or "").strip()
            if t:
                out.append(t)
        else:
            table_idx += 1
            tbl: Table = block
            nrows = len(tbl.rows)
            ncols = max((len(r.cells) for r in tbl.rows), default=0)
            out.append(f"[DOCX_V2_TABLE idx={table_idx} rows={nrows} cols={ncols}]")
            for ri, row in enumerate(tbl.rows):
                parts: list[str] = []
                for ci, cell in enumerate(row.cells):
                    cell_text = (cell.text or "").strip()
                    fill = _cell_shd_fill(cell)
                    mark_parts: list[str] = []
                    if _is_candidate_shading(fill, candidate_fills):
                        mark_parts.append(f"[超标候选·底纹={fill}]" if fill else "[超标候选]")
                    color_marks: list[str] = []
                    if fill and fill != "FFFFFF":
                        # 候选底纹已在「超标候选」中写出，避免同一单元格再重复 底纹= 占用 token
                        if not _is_candidate_shading(fill, candidate_fills):
                            color_marks.append(f"底纹={fill}")
                    color_marks.extend(_collect_cell_run_color_marks(cell))
                    color_marks = _dedupe_keep_order(color_marks)
                    if color_marks:
                        mark_parts.append(f"[颜色标注:{','.join(color_marks)}]")
                    parts.append(f"c{ci}='{_escape_cell_text(cell_text)}'{''.join(mark_parts)}")
                out.append(f"r{ri}: " + " | ".join(parts))
            out.append("")
    return "\n".join(out).strip() + ("\n" if out else "")
