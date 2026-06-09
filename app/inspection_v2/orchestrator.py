"""检修 V2 编排入口：分块策略路由（与 V1 服务解耦，便于单独演进/移除）。"""

from __future__ import annotations

from typing import Any

from app.inspection_v2.legacy_parse_chunks import split_legacy_parse_chunks
from app.inspection_v2.processing_units import split_docx_v2_by_processing_units


def v2_docx_chunk_params(cfg: Any) -> dict[str, int | bool]:
    """从 inspection_extract 配置读取 DOCX V2 分块参数。"""
    return {
        "max_chunk_chars": max(2000, int(getattr(cfg, "v2_parse_unit_max_chars", 6000))),
        "v2_table_row_window_enabled": bool(getattr(cfg, "v2_table_row_window_enabled", True)),
        "v2_table_data_rows_per_window": max(
            1, int(getattr(cfg, "v2_table_data_rows_per_window", 20))
        ),
        "v2_table_column_split_enabled": bool(
            getattr(cfg, "v2_table_column_split_enabled", False)
        ),
    }


def split_parse_chunks(
    parsed_text: str,
    *,
    parse_route: str,
    max_chunk_chars: int,
    v2_table_row_window_enabled: bool = True,
    v2_table_data_rows_per_window: int = 20,
    v2_table_column_split_enabled: bool = False,
) -> list[str]:
    route = (parse_route or "text").strip().lower()
    if route == "docx_v2":
        return split_docx_v2_by_processing_units(
            parsed_text,
            max_chunk_chars=max_chunk_chars,
            table_row_window_enabled=v2_table_row_window_enabled,
            table_data_rows_per_window=v2_table_data_rows_per_window,
            table_column_split_enabled=v2_table_column_split_enabled,
        )
    return split_legacy_parse_chunks(parsed_text, max_chunk_chars=max_chunk_chars)
