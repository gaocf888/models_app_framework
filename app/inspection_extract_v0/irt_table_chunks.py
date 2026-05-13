"""
检修 V0：按 IRT 中的 `tables` 切块供 LLM 并行抽取；无表时退化为单块（与旧行为兼容）。
"""

from __future__ import annotations

import json
import re
from typing import Any


def count_llm_table_chunks(irt: dict[str, Any] | None) -> int:
    """用于异步任务分块列表：根据已落盘的 IRT 估计 LLM 分块数。"""
    if not isinstance(irt, dict):
        return 1
    tabs = irt.get("tables")
    if isinstance(tabs, list) and len(tabs) >= 1:
        return len(tabs)
    return 1


def _blocks_for_page(
    blocks: list[dict[str, Any]],
    *,
    page_no: int,
    max_blocks: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        try:
            pn = int(b.get("page_no") or 1)
        except (TypeError, ValueError):
            pn = 1
        if pn == page_no:
            out.append(b)
        if len(out) >= max_blocks:
            break
    return out


def _bbox_overlap(block: dict[str, Any], table_bbox: dict[str, float] | None) -> bool:
    if not table_bbox:
        return True
    bb = block.get("bbox")
    if not isinstance(bb, dict):
        return True
    try:
        bx1, by1 = float(bb.get("x1", 0)), float(bb.get("y1", 0))
        bx2, by2 = float(bb.get("x2", 0)), float(bb.get("y2", 0))
        tx1, ty1 = float(table_bbox["x1"]), float(table_bbox["y1"])
        tx2, ty2 = float(table_bbox["x2"]), float(table_bbox["y2"])
    except (TypeError, ValueError, KeyError):
        return True
    if bx2 < tx1 or bx1 > tx2 or by2 < ty1 or by1 > ty2:
        return False
    return True


def _split_docx_table_segments(parsed_text: str) -> list[str]:
    """按 `[DOCX_TABLE` 切分原生 docx 解析文本，与表格块顺序对齐（尽力而为）。"""
    t = (parsed_text or "").strip()
    if not t or "[DOCX_TABLE" not in t:
        return []
    parts = re.split(r"(?=\[DOCX_TABLE)", t)
    return [p.strip() for p in parts if p.strip().startswith("[DOCX_TABLE")]


def build_llm_work_items(
    irt: dict[str, Any],
    parsed_text: str,
    *,
    max_blocks_per_chunk: int,
    global_snippet_chars: int = 2000,
) -> list[dict[str, Any]]:
    """
    构造并行 LLM 工作项。

    每项: work_idx, table_id, user_body（完整 user 消息正文，不含 system）。
    """
    if not isinstance(irt, dict):
        irt = {}
    blocks_in = irt.get("blocks") if isinstance(irt.get("blocks"), list) else []
    blocks: list[dict[str, Any]] = [b for b in blocks_in if isinstance(b, dict)]
    tables_in = irt.get("tables") if isinstance(irt.get("tables"), list) else []
    tables: list[dict[str, Any]] = [t for t in tables_in if isinstance(t, dict)]

    pages = [p for p in (irt.get("pages") or []) if isinstance(p, dict)]
    base_meta = {
        "irt_version": irt.get("irt_version"),
        "parse_route": irt.get("parse_route"),
        "engine_version": irt.get("engine_version"),
        "ocr_engine": irt.get("ocr_engine"),
        "layout_engine": irt.get("layout_engine"),
    }

    docx_segments = _split_docx_table_segments(parsed_text)
    head_snippet = (parsed_text or "")[:global_snippet_chars]

    if not tables:
        slim = blocks[:80] if len(blocks) > 80 else blocks
        chunk_irt = {**base_meta, "pages": pages, "blocks": slim, "tables": []}
        body = (
            "【IRT】\n"
            + json.dumps(chunk_irt, ensure_ascii=False)[:24000]
            + "\n\n【原文片段】\n"
            + (parsed_text or "")[:6000]
        )
        return [{"work_idx": 1, "table_id": None, "user_body": body}]

    items: list[dict[str, Any]] = []
    for i, tbl in enumerate(tables, start=1):
        tid = str(tbl.get("table_id") or f"t{i}")
        try:
            pno = int(tbl.get("page_no") or 1)
        except (TypeError, ValueError):
            pno = 1
        bbox = tbl.get("bbox") if isinstance(tbl.get("bbox"), dict) else None
        if pages:
            page_slice = [p for p in pages if int(p.get("page_no") or 1) == pno]
            if not page_slice:
                page_slice = [pages[0]]
        else:
            page_slice = []
        page_blocks: list[dict[str, Any]] = []
        for b in blocks:
            try:
                bn = int(b.get("page_no") or 1)
            except (TypeError, ValueError):
                bn = 1
            if bn != pno:
                continue
            if _bbox_overlap(b, bbox):
                page_blocks.append(b)
            if len(page_blocks) >= max_blocks_per_chunk:
                break
        chunk_irt = {
            **base_meta,
            "pages": page_slice,
            "blocks": page_blocks,
            "tables": [tbl],
        }
        seg = docx_segments[i - 1] if len(docx_segments) >= i else ""
        rows = tbl.get("rows") if isinstance(tbl.get("rows"), list) else []
        rows_txt = ""
        if rows:
            lines = []
            for row in rows:
                if isinstance(row, list):
                    lines.append(" | ".join(str(c) for c in row))
                elif row:
                    lines.append(str(row))
            rows_txt = "\n".join(lines)[:8000]
        snippet = head_snippet
        if seg:
            snippet = (head_snippet + "\n\n【与本表相邻的 Word 表格序列化】\n" + seg)[:12000]
        body = (
            "【IRT·本工作项仅含一张表，请从该表及关联 blocks 抽取 records】\n"
            + json.dumps(chunk_irt, ensure_ascii=False)[:24000]
            + "\n\n【表格行文本（rows 展开）】\n"
            + (rows_txt or "（无）")
            + "\n\n【原文片段】\n"
            + snippet
        )
        items.append({"work_idx": i, "table_id": tid, "user_body": body})
    return items
