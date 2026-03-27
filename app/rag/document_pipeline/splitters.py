from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class ChunkingConfig:
    chunk_size: int = 500
    chunk_overlap: int = 80
    min_chunk_size: int = 40


class StructureSplitter:
    _heading_re = re.compile(r"^\s{0,3}(#{1,6}\s+.+|[0-9]+(\.[0-9]+)*\s+.+)$")

    def split(self, text: str) -> List[str]:
        sections: list[str] = []
        buf: list[str] = []
        for line in text.splitlines():
            if self._heading_re.match(line) and buf:
                sections.append("\n".join(buf).strip())
                buf = [line]
            else:
                buf.append(line)
        if buf:
            sections.append("\n".join(buf).strip())
        return [s for s in sections if s]


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

