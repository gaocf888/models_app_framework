from __future__ import annotations

import re
from typing import Tuple

_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def format_figure_chunk_text(
    *,
    caption: str,
    neighbor_before: str | None = None,
    neighbor_after: str | None = None,
    section_label: str | None = None,
    doc_name: str | None = None,
) -> str:
    parts: list[str] = []
    nb = (neighbor_before or "").strip()
    na = (neighbor_after or "").strip()
    if nb:
        parts.append(f"【邻近正文-前】{nb}")
    if na:
        parts.append(f"【邻近正文-后】{na}")
    cap = (caption or "").strip()
    if section_label or doc_name:
        header = "【图块"
        if section_label:
            header += f"-{section_label}"
        header += "】"
        if doc_name:
            header += f"文档《{doc_name}》"
            if section_label:
                header += f" {section_label}"
        if cap and not cap.startswith("【"):
            parts.append(f"{header}\n\n{cap}")
        elif cap:
            parts.append(cap)
        else:
            parts.append(header)
    elif cap:
        parts.append(cap)
    return "\n\n".join(p for p in parts if p.strip()).strip()


def slice_neighbor_text(
    full_text: str,
    *,
    anchor_start: int,
    anchor_end: int,
    max_chars: int,
    before_ratio: float = 0.7,
) -> Tuple[str, str]:
    """在 full_text 中按锚点 [anchor_start, anchor_end) 截取图前/图后邻近正文。"""
    if max_chars <= 0:
        return "", ""
    text = full_text or ""
    before_budget = max(0, int(max_chars * max(0.0, min(1.0, before_ratio))))
    after_budget = max(0, max_chars - before_budget)
    before_region = text[: max(0, anchor_start)]
    after_region = text[min(len(text), anchor_end) :]
    before = before_region[-before_budget:].strip() if before_budget else ""
    after = after_region[:after_budget].strip() if after_budget else ""
    return before, after


def find_markdown_image_refs(parsed_text: str) -> list[tuple[int, int, str, str]]:
    """
    返回 [(match_start, match_end, alt_text, image_ref), ...] 按出现顺序。
    image_ref 为 markdown 括号内路径（可为相对路径或 URL）。
    """
    out: list[tuple[int, int, str, str]] = []
    for m in _MD_IMAGE_RE.finditer(parsed_text or ""):
        full = m.group(0)
        ref = (m.group(1) or "").strip()
        alt_m = re.match(r"!\[(.*?)\]", full)
        alt = alt_m.group(1) if alt_m else ""
        if ref:
            out.append((m.start(), m.end(), alt, ref))
    return out


def resolve_image_ref_path(ref: str, *, base_dir: str | None = None) -> str | None:
    from pathlib import Path
    from urllib.parse import urlparse

    raw = (ref or "").strip().strip('"').strip("'")
    if not raw:
        return None
    low = raw.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return raw
    if low.startswith("file://"):
        p = Path(raw[7:])
        return str(p) if p.is_file() else None
    p = Path(raw)
    if p.is_file():
        return str(p.resolve())
    if base_dir:
        candidate = Path(base_dir) / raw.lstrip("./")
        if candidate.is_file():
            return str(candidate.resolve())
    return None
