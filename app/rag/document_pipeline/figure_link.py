from __future__ import annotations

import re

from app.rag.models import ChunkRecord

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


def _section_path_for_chunk(chunk: ChunkRecord) -> str | None:
    meta = chunk.metadata or {}
    for key in ("section_path", "parent_section_path"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    for line in (chunk.text or "").splitlines()[:4]:
        m = _HEADING_RE.match(line.strip())
        if m:
            return m.group(2).strip()
    return None


def _pick_parent_text_chunk(fig: ChunkRecord, sorted_text: list[ChunkRecord]) -> ChunkRecord:
    """
    figure.parent_chunk_id = 图前最近 text chunk；同 section_path 优先（§5.4.2）。
    图前无 text 时取同 section 第一个 text chunk，否则取全文第一个 text chunk。
    """
    fig_section = (fig.metadata or {}).get("parent_section_path")
    if isinstance(fig_section, str):
        fig_section = fig_section.strip() or None
    else:
        fig_section = None

    before = [tc for tc in sorted_text if tc.chunk_index < fig.chunk_index]
    if fig_section:
        same_before = [tc for tc in before if _section_path_for_chunk(tc) == fig_section]
        if same_before:
            return same_before[-1]
        same_any = [tc for tc in sorted_text if _section_path_for_chunk(tc) == fig_section]
        if same_any:
            return same_any[0]
    if before:
        return before[-1]
    return sorted_text[0]


def merge_and_link(text_chunks: list[ChunkRecord], figure_chunks: list[ChunkRecord]) -> list[ChunkRecord]:
    """
    建立 figure ↔ 正文双向关联：
    - figure.metadata.parent_chunk_id = 图前最近 text chunk（优先同 section_path）
    - text.metadata.related_figure_ids += figure.chunk_id
    """
    if not figure_chunks:
        return list(text_chunks)
    if not text_chunks:
        return list(figure_chunks)

    sorted_text = sorted(text_chunks, key=lambda c: c.chunk_index)
    sorted_figures = sorted(figure_chunks, key=lambda c: c.chunk_index)

    for fig in sorted_figures:
        parent = _pick_parent_text_chunk(fig, sorted_text)
        fig.metadata["parent_chunk_id"] = parent.chunk_id
        rel = list(parent.metadata.get("related_figure_ids") or [])
        if fig.chunk_id not in rel:
            rel.append(fig.chunk_id)
        parent.metadata["related_figure_ids"] = rel

    return list(text_chunks) + list(figure_chunks)
