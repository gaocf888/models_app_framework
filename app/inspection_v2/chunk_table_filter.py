"""是否含表格：用于异步任务仅对含表分块调 LLM，与 docx_v2 / legacy 分块格式对齐。"""

from __future__ import annotations


_DOCX_V2_TABLE_MARK = "[DOCX_V2_TABLE"


def chunk_contains_table(chunk: str, *, parse_route: str) -> bool:
    pr = (parse_route or "text").strip().lower()
    if pr == "docx_v2":
        return _DOCX_V2_TABLE_MARK in (chunk or "")
    return _legacy_chunk_looks_like_table(chunk)


def _legacy_chunk_looks_like_table(chunk: str) -> bool:
    """legacy 分块：至少两行含 | 视为表格上下文。"""
    lines = [ln for ln in (chunk or "").splitlines() if "|" in ln]
    return len(lines) >= 2


def filter_table_work_items(chunks: list[str], *, parse_route: str) -> list[tuple[int, str]]:
    """
    仅保留含表格的分块，按顺序编号 work_idx=1..N。
    返回 [(work_idx, chunk_text), ...]。
    """
    out: list[tuple[int, str]] = []
    for c in chunks:
        if chunk_contains_table(c, parse_route=parse_route):
            out.append((len(out) + 1, c))
    return out
