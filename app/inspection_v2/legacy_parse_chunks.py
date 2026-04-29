"""与既有 InspectionExtractService 一致的通用分块（非 docx_v2）。"""

from __future__ import annotations


def split_legacy_parse_chunks(parsed_text: str, *, max_chunk_chars: int) -> list[str]:
    import re

    text = (parsed_text or "").strip()
    if not text:
        return []
    lines = [x for x in text.splitlines()]
    chunks: list[str] = []

    i = 0
    while i < len(lines):
        if "|" not in lines[i]:
            i += 1
            continue
        j = i
        while j < len(lines) and "|" in lines[j]:
            j += 1
        if j - i >= 2:
            start = max(0, i - 8)
            end = min(len(lines), j + 2)
            chunk = "\n".join(lines[start:end]).strip()
            if chunk:
                chunks.append(chunk)
        i = j

    if not chunks:
        step = max(1000, max_chunk_chars)
        p = 0
        while p < len(text):
            chunks.append(text[p : p + step])
            p += step

    normalized: list[str] = []
    for c in chunks:
        if len(c) <= max_chunk_chars:
            normalized.append(c)
            continue
        p = 0
        while p < len(c):
            normalized.append(c[p : p + max_chunk_chars])
            p += max_chunk_chars
    return normalized or [text[:max_chunk_chars]]
