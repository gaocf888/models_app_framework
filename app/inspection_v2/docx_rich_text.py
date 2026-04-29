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
    将 docx 按文档流展开为供 LLM 使用的文本：段落原样，表格按行列输出，
    并在命中底纹色时附加「超标候选」标记。
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
                    mark = ""
                    if _is_candidate_shading(fill, candidate_fills):
                        mark = f"[超标候选·底纹={fill}]" if fill else "[超标候选]"
                    parts.append(f"c{ci}='{_escape_cell_text(cell_text)}'{mark}")
                out.append(f"r{ri}: " + " | ".join(parts))
            out.append("")
    return "\n".join(out).strip() + ("\n" if out else "")
