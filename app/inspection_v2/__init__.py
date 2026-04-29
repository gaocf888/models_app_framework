"""
检修报告 V2：摄入、Processing Unit 分块、编排入口（与 RAG DocumentParser 扁平 docx 路径隔离）。
"""

from app.inspection_v2.docx_rich_text import normalize_shading_fill, serialize_docx_for_inspection_v2
from app.inspection_v2.orchestrator import split_parse_chunks
from app.inspection_v2.record_normalization import apply_deterministic_rules_to_record

__all__ = [
    "apply_deterministic_rules_to_record",
    "normalize_shading_fill",
    "serialize_docx_for_inspection_v2",
    "split_parse_chunks",
]
