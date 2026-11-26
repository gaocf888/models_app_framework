# -*- coding: utf-8 -*-
"""Generate styled reference.docx and convert deliverable Markdown to Word."""
from __future__ import annotations

import subprocess
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
REF = OUT_DIR / "_reference_styles.docx"


def _set_run_font(run, east_asia: str, ascii_font: str, size_pt: float | None = None, bold=None):
    run.font.name = ascii_font
    r = run._element
    rPr = r.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), ascii_font)
    rFonts.set(qn("w:hAnsi"), ascii_font)
    rFonts.set(qn("w:eastAsia"), east_asia)
    rFonts.set(qn("w:cs"), ascii_font)
    if size_pt is not None:
        run.font.size = Pt(size_pt)
    if bold is not None:
        run.font.bold = bold


def _style_font(style, east_asia: str, ascii_font: str, size_pt: float, bold=False, color=None):
    font = style.font
    font.name = ascii_font
    font.size = Pt(size_pt)
    font.bold = bold
    if color is not None:
        font.color.rgb = color
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"), ascii_font)
    rFonts.set(qn("w:hAnsi"), ascii_font)
    rFonts.set(qn("w:eastAsia"), east_asia)
    rFonts.set(qn("w:cs"), ascii_font)


def build_reference() -> Path:
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)

    styles = doc.styles

    normal = styles["Normal"]
    _style_font(normal, "宋体", "Times New Roman", 12)
    pf = normal.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    pf.space_after = Pt(6)
    pf.first_line_indent = Cm(0)

    heading_specs = [
        ("Heading 1", 18, True, RGBColor(0x1F, 0x4E, 0x79), Pt(18), Pt(10)),
        ("Heading 2", 15, True, RGBColor(0x2E, 0x75, 0xB6), Pt(14), Pt(8)),
        ("Heading 3", 13, True, RGBColor(0x5B, 0x9B, 0xD5), Pt(10), Pt(6)),
        ("Heading 4", 12, True, RGBColor(0x5B, 0x9B, 0xD5), Pt(8), Pt(4)),
    ]
    for name, size, bold, color, before, after in heading_specs:
        st = styles[name]
        _style_font(st, "微软雅黑", "Calibri", size, bold=bold, color=color)
        st.paragraph_format.space_before = before
        st.paragraph_format.space_after = after
        st.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
        st.paragraph_format.first_line_indent = Cm(0)

    title = styles["Title"]
    _style_font(title, "微软雅黑", "Calibri", 22, bold=True, color=RGBColor(0x1F, 0x4E, 0x79))
    title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_after = Pt(12)

    for qname in ("Quote", "Intense Quote"):
        if qname in styles:
            qs = styles[qname]
            _style_font(qs, "楷体", "Georgia", 11, bold=False, color=RGBColor(0x59, 0x59, 0x59))
            qs.paragraph_format.left_indent = Cm(0.5)
            qs.paragraph_format.space_before = Pt(6)
            qs.paragraph_format.space_after = Pt(6)

    try:
        code = styles["Source Code"]
    except KeyError:
        code = styles.add_style("Source Code", WD_STYLE_TYPE.PARAGRAPH)
    _style_font(code, "Consolas", "Consolas", 9)
    code.paragraph_format.line_spacing = 1.0
    code.paragraph_format.space_before = Pt(4)
    code.paragraph_format.space_after = Pt(4)
    code.paragraph_format.first_line_indent = Cm(0)

    if "Body Text" in styles:
        bt = styles["Body Text"]
        _style_font(bt, "宋体", "Times New Roman", 12)
        bt.paragraph_format.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE

    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    table.cell(0, 0).text = "示例列"
    table.cell(0, 1).text = "示例值"
    for cell in table.rows[0].cells:
        for p in cell.paragraphs:
            for run in p.runs:
                _set_run_font(run, "宋体", "Times New Roman", 10.5)

    doc.add_paragraph("参考样式文档（生成 Word 时由 pandoc 引用）。")
    doc.save(REF)
    return REF


def _add_cover_and_pagebreak(doc: Document, doc_title: str, subtitle: str) -> None:
    """用 XML 在文档最前插入封面 + 分页。"""
    body = doc.element.body

    def make_p(text: str, *, center=False, size=26, bold=True, east="微软雅黑", color=None, space_before=0):
        p = OxmlElement("w:p")
        pPr = OxmlElement("w:pPr")
        if center:
            jc = OxmlElement("w:jc")
            jc.set(qn("w:val"), "center")
            pPr.append(jc)
        if space_before:
            sp = OxmlElement("w:spacing")
            sp.set(qn("w:before"), str(space_before))
            pPr.append(sp)
        p.append(pPr)
        r = OxmlElement("w:r")
        rPr = OxmlElement("w:rPr")
        rFonts = OxmlElement("w:rFonts")
        rFonts.set(qn("w:ascii"), "Calibri")
        rFonts.set(qn("w:hAnsi"), "Calibri")
        rFonts.set(qn("w:eastAsia"), east)
        rPr.append(rFonts)
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), str(int(size * 2)))
        rPr.append(sz)
        szCs = OxmlElement("w:szCs")
        szCs.set(qn("w:val"), str(int(size * 2)))
        rPr.append(szCs)
        if bold:
            rPr.append(OxmlElement("w:b"))
        if color is not None:
            c = OxmlElement("w:color")
            c.set(qn("w:val"), color)
            rPr.append(c)
        r.append(rPr)
        t = OxmlElement("w:t")
        t.set(qn("xml:space"), "preserve")
        t.text = text
        r.append(t)
        p.append(r)
        return p

    page_break = OxmlElement("w:p")
    r = OxmlElement("w:r")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    r.append(br)
    page_break.append(r)

    nodes = [
        make_p("", center=True, size=12, bold=False, space_before=0),
        make_p(doc_title, center=True, size=26, bold=True, east="微软雅黑", color="1F4E79", space_before=2400),
        make_p(subtitle, center=True, size=12, bold=False, east="楷体", color="595959", space_before=200),
        make_p("结项交付文档 · Word 版", center=True, size=11, bold=False, east="宋体", color="666666", space_before=400),
        page_break,
    ]
    # insert in reverse at position 0
    for node in reversed(nodes):
        body.insert(0, node)


def post_process(docx_path: Path, doc_title: str, subtitle: str) -> None:
    doc = Document(str(docx_path))
    _add_cover_and_pagebreak(doc, doc_title, subtitle)
    for section in doc.sections:
        section.left_margin = Cm(2.5)
        section.right_margin = Cm(2.5)
        section.top_margin = Cm(2.54)
        section.bottom_margin = Cm(2.54)

        header = section.header
        header.is_linked_to_previous = False
        hp = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
        hp.clear()
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = hp.add_run(doc_title)
        _set_run_font(run, "微软雅黑", "Calibri", 9)
        run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        footer = section.footer
        footer.is_linked_to_previous = False
        fp = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
        fp.clear()
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r1 = fp.add_run("第 ")
        _set_run_font(r1, "宋体", "Times New Roman", 9)
        fld_begin = OxmlElement("w:fldChar")
        fld_begin.set(qn("w:fldCharType"), "begin")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = " PAGE "
        fld_end = OxmlElement("w:fldChar")
        fld_end.set(qn("w:fldCharType"), "end")
        r2 = fp.add_run()
        _set_run_font(r2, "宋体", "Times New Roman", 9)
        r2._r.append(fld_begin)
        r2._r.append(instr)
        r2._r.append(fld_end)
        r3 = fp.add_run(" 页")
        _set_run_font(r3, "宋体", "Times New Roman", 9)

    styles = doc.styles
    _style_font(styles["Normal"], "宋体", "Times New Roman", 12)
    for name, size, color in (
        ("Heading 1", 18, RGBColor(0x1F, 0x4E, 0x79)),
        ("Heading 2", 15, RGBColor(0x2E, 0x75, 0xB6)),
        ("Heading 3", 13, RGBColor(0x5B, 0x9B, 0xD5)),
    ):
        if name in styles:
            _style_font(styles[name], "微软雅黑", "Calibri", size, bold=True, color=color)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    for run in p.runs:
                        if run.font.size is None or run.font.size > Pt(11):
                            _set_run_font(run, "宋体", "Times New Roman", 10.5)

    doc.save(str(docx_path))


def convert(md_path: Path, out_name: str, title: str, subtitle: str) -> Path:
    out = OUT_DIR / out_name
    cmd = [
        "pandoc",
        str(md_path),
        "-f",
        "markdown+pipe_tables+fenced_code_blocks+strikeout+task_lists+auto_identifiers",
        "-t",
        "docx",
        "-o",
        str(out),
        f"--reference-doc={REF}",
        "--toc",
        "--toc-depth=3",
        "--metadata",
        f"title={title}",
        "--resource-path",
        str(md_path.parent),
    ]
    print("RUN:", " ".join(cmd))
    subprocess.run(cmd, check=True)
    post_process(out, title, subtitle)
    return out


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    build_reference()
    files = [
        (
            ROOT / "deploy-docs" / "整体部署运维手册.md",
            "整体部署运维手册.docx",
            "整体部署运维手册",
            "读者：实施、运维 · 结项在线栈部署与验收",
        ),
        (
            ROOT / "deploy-docs" / "源码移交说明.md",
            "源码移交说明.docx",
            "源码移交说明",
            "读者：接方开发 · 接口 / Prompt / 业务配置与链路排查",
        ),
    ]
    for md, name, title, subtitle in files:
        if not md.exists():
            raise SystemExit(f"missing: {md}")
        out = convert(md, name, title, subtitle)
        print(f"OK {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
