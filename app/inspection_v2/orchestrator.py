"""检修 V2 编排入口：分块策略路由（与 V1 服务解耦，便于单独演进/移除）。"""

from __future__ import annotations

from app.inspection_v2.legacy_parse_chunks import split_legacy_parse_chunks
from app.inspection_v2.processing_units import split_docx_v2_by_processing_units


def split_parse_chunks(
    parsed_text: str,
    *,
    parse_route: str,
    max_chunk_chars: int,
) -> list[str]:
    route = (parse_route or "text").strip().lower()
    if route == "docx_v2":
        return split_docx_v2_by_processing_units(parsed_text, max_chunk_chars=max_chunk_chars)
    return split_legacy_parse_chunks(parsed_text, max_chunk_chars=max_chunk_chars)
