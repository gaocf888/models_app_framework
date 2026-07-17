"""
文档章节标题识别与规范化。

供 StructureSplitter、DocumentPipeline、DOCX 解析与 figure 邻近章节共用，
保证摄入写入的 ``section_path`` 与 rag_citations 展示口径一致。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
# 展示用章节路径最大长度（字符），避免脏标题撑爆 metadata
SECTION_PATH_MAX_CHARS = 120

# Markdown AT1–H6
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")

# 阿拉伯数字编号：1 / 1.2 / 1.2.3 后接空白与标题正文
_NUMBERED_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+?)\s*$")

# 中文「第X章/节/条」
_CN_CHAPTER_RE = re.compile(
    r"^(第[一二三四五六七八九十百千零〇两\d]+[章节条款篇部])\s*(.*)$"
)

# 中文顿号序号：一、二、 … 或 一. 二．
_CN_ENUM_RE = re.compile(r"^([一二三四五六七八九十百]+)\s*[、．.]\s*(.+?)\s*$")

# 标题行过长则不当作标题（降低误伤正文列表）
_MAX_HEADING_LINE_CHARS = 80

# DOCX 样式名 → Markdown 级别
_DOCX_HEADING_STYLE_RE = re.compile(
    r"^(?:Heading|标题)\s*([1-6])$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HeadingMatch:
    """单行标题解析结果。"""

    raw_line: str
    title: str
    level: int
    section_path: str


@dataclass(frozen=True)
class SectionBlock:
    """结构切分后的一节：正文 + 归属章节。"""

    text: str
    section_path: str | None
    section_level: int | None = None


def normalize_heading_title(title: str, *, max_chars: int = SECTION_PATH_MAX_CHARS) -> str:
    """去掉 Markdown # 前缀与多余空白，并截断过长标题。"""
    t = (title or "").strip()
    t = re.sub(r"^#{1,6}\s*", "", t).strip()
    t = re.sub(r"\s+", " ", t)
    if not t:
        return ""
    if len(t) > max_chars:
        return t[: max_chars - 1].rstrip() + "…"
    return t


def parse_heading_line(line: str) -> HeadingMatch | None:
    """
    判断一行是否为章节标题；是则返回规范化结果，否则 None。

    识别顺序：Markdown → 编号标题 → 「第X章」→ 中文顿号序号。
    """
    raw = (line or "").rstrip("\r\n")
    stripped = raw.strip()
    if not stripped or len(stripped) > _MAX_HEADING_LINE_CHARS:
        return None

    m = _MD_HEADING_RE.match(stripped)
    if m:
        level = len(m.group(1))
        title = normalize_heading_title(m.group(2))
        if not title:
            return None
        return HeadingMatch(raw_line=raw, title=title, level=level, section_path=title)

    m = _NUMBERED_HEADING_RE.match(stripped)
    if m:
        nums = m.group(1)
        body = normalize_heading_title(m.group(2))
        if not body or not _looks_like_numbered_heading_body(body):
            return None
        level = nums.count(".") + 1
        section_path = normalize_heading_title(f"{nums} {body}")
        return HeadingMatch(raw_line=raw, title=body, level=level, section_path=section_path)

    m = _CN_CHAPTER_RE.match(stripped)
    if m:
        prefix = m.group(1).strip()
        rest = normalize_heading_title(m.group(2) or "")
        section_path = normalize_heading_title(f"{prefix} {rest}".strip() if rest else prefix)
        if not section_path:
            return None
        level = _cn_chapter_level(prefix)
        return HeadingMatch(raw_line=raw, title=section_path, level=level, section_path=section_path)

    m = _CN_ENUM_RE.match(stripped)
    if m:
        enum = m.group(1)
        body = normalize_heading_title(m.group(2))
        if not body or not _looks_like_numbered_heading_body(body):
            return None
        section_path = normalize_heading_title(f"{enum}、{body}")
        return HeadingMatch(raw_line=raw, title=body, level=1, section_path=section_path)

    return None


def is_heading_line(line: str) -> bool:
    return parse_heading_line(line) is not None


def section_path_before_offset(text: str, offset: int) -> tuple[str | None, int | None]:
    """
    在 ``text[:offset]`` 中取最近一个标题的 (section_path, level)。
    用于 window 策略按字符偏移标注章节。
    """
    if not text or offset <= 0:
        return None, None
    region = text[: min(len(text), max(0, offset))]
    last: HeadingMatch | None = None
    for line in region.splitlines():
        hm = parse_heading_line(line)
        if hm is not None:
            last = hm
    if last is None:
        return None, None
    return last.section_path, last.level


def docx_style_to_markdown_heading(style_name: str | None, paragraph_text: str) -> str | None:
    """
    将 DOCX Heading / 标题 N 样式转为 Markdown 标题行（含 # 前缀）。
    非标题样式返回 None。
    """
    name = (style_name or "").strip()
    text = (paragraph_text or "").strip()
    if not name or not text:
        return None
    m = _DOCX_HEADING_STYLE_RE.match(name)
    if not m:
        # 兼容 "Heading 1 Char" 等：取前缀
        m = re.match(r"^(?:Heading|标题)\s*([1-6])\b", name, re.IGNORECASE)
    if not m:
        return None
    level = max(1, min(6, int(m.group(1))))
    title = normalize_heading_title(text)
    if not title:
        return None
    return f"{'#' * level} {title}"


def _looks_like_numbered_heading_body(body: str) -> bool:
    """编号后正文过短或纯数字/标点时不当标题，降低列表误伤。"""
    b = body.strip()
    if len(b) < 2:
        return False
    if re.fullmatch(r"[\d\.\-\s]+", b):
        return False
    return True


def _cn_chapter_level(prefix: str) -> int:
    if "章" in prefix or "篇" in prefix or "部" in prefix:
        return 1
    if "节" in prefix:
        return 2
    if "条" in prefix or "款" in prefix:
        return 3
    return 1


def find_chunk_start_offset(full_text: str, chunk: str, *, search_from: int = 0) -> int:
    """
    在全文中定位 chunk 起始偏移；优先从 search_from 起找，失败则全文找，再失败返回 search_from。
    """
    if not full_text or not chunk:
        return max(0, search_from)
    needle = chunk[: min(64, len(chunk))]
    idx = full_text.find(needle, max(0, search_from))
    if idx >= 0:
        return idx
    idx = full_text.find(needle)
    if idx >= 0:
        return idx
    return max(0, search_from)
