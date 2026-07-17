from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List

from app.rag.document_pipeline.section_utils import (
    SectionBlock,
    parse_heading_line,
    section_path_before_offset,
    find_chunk_start_offset,
)


@dataclass
class ChunkingConfig:
    chunk_size: int = 500
    chunk_overlap: int = 80
    min_chunk_size: int = 40


class StructureSplitter:
    """
    按标题结构切分，并为每一节附带 ``section_path`` / ``section_level``。

    标题识别见 ``section_utils.parse_heading_line``（Markdown / 编号 / 中文章节）。
    """

    def split(self, text: str) -> List[str]:
        """兼容旧接口：仅返回各节文本。"""
        return [b.text for b in self.split_sections(text)]

    def split_sections(self, text: str) -> List[SectionBlock]:
        if not (text or "").strip():
            return []

        sections: list[SectionBlock] = []
        buf: list[str] = []
        current_path: str | None = None
        current_level: int | None = None

        def _flush() -> None:
            nonlocal buf, current_path, current_level
            body = "\n".join(buf).strip()
            if body:
                sections.append(
                    SectionBlock(text=body, section_path=current_path, section_level=current_level)
                )
            buf = []

        for line in text.splitlines():
            hm = parse_heading_line(line)
            if hm is not None and buf:
                _flush()
                current_path = hm.section_path
                current_level = hm.level
                buf = [line]
            elif hm is not None and not buf:
                # 文首即标题：开启新节，章节归属该标题
                current_path = hm.section_path
                current_level = hm.level
                buf = [line]
            else:
                buf.append(line)

        _flush()
        return sections


class WindowSplitter:
    def __init__(self, cfg: ChunkingConfig) -> None:
        if cfg.chunk_overlap >= cfg.chunk_size:
            raise ValueError("chunk_overlap must be smaller than chunk_size")
        self._cfg = cfg

    def split(self, text: str) -> List[str]:
        if not text:
            return []
        chunk_size = self._cfg.chunk_size
        overlap = self._cfg.chunk_overlap
        min_chunk_size = self._cfg.min_chunk_size
        step = chunk_size - overlap

        chunks: list[str] = []
        n = len(text)
        start = 0
        while start < n:
            end = min(start + chunk_size, n)
            ch = text[start:end].strip()
            if ch:
                chunks.append(ch)
            if end >= n:
                break
            start += step
        if len(chunks) >= 2 and len(chunks[-1]) < min_chunk_size:
            chunks[-2] = f"{chunks[-2]}\n{chunks[-1]}".strip()
            chunks.pop()
        return chunks

    def split_with_sections(self, text: str) -> List[SectionBlock]:
        """
        整篇滑窗切分，并按每个窗口在全文中的起始偏移标注最近标题章节。
        """
        raw_chunks = self.split(text)
        if not raw_chunks:
            return []
        out: list[SectionBlock] = []
        cursor = 0
        for ch in raw_chunks:
            offset = find_chunk_start_offset(text, ch, search_from=cursor)
            path, level = section_path_before_offset(text, offset + 1)
            # 窗口内若以标题开头，优先用该标题
            first_line = ch.splitlines()[0] if ch.splitlines() else ""
            hm = parse_heading_line(first_line)
            if hm is not None:
                path, level = hm.section_path, hm.level
            out.append(SectionBlock(text=ch, section_path=path, section_level=level))
            cursor = offset + max(1, len(ch) - self._cfg.chunk_overlap)
        return out


class SemanticSplitter:
    _sent_re = re.compile(r"(?<=[。！？!?\.])\s+")

    def split(self, text: str, target_size: int) -> List[str]:
        sentences = [s.strip() for s in self._sent_re.split(text) if s.strip()]
        if not sentences:
            return []
        chunks: list[str] = []
        buf = []
        size = 0
        for s in sentences:
            slen = len(s)
            if buf and size + slen > target_size:
                chunks.append(" ".join(buf).strip())
                buf = [s]
                size = slen
            else:
                buf.append(s)
                size += slen
        if buf:
            chunks.append(" ".join(buf).strip())
        return [c for c in chunks if c]
