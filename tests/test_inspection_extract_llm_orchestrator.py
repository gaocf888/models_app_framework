from __future__ import annotations

from app.services.inspection_extract_llm_orchestrator import (
    _extract_records_from_ndjson,
    _salvage_records_from_truncated_json,
    _summarize_chunk,
)


def test_extract_records_from_ndjson_lines() -> None:
    raw = """
{"检测位置":"右墙","行号":"通用","管号":"-5","壁厚":4.7}
{"检测位置":"右墙","行号":"通用","管号":"-6","壁厚":5.0}
"""
    out = _extract_records_from_ndjson(raw)
    assert len(out) == 2
    assert out[0]["管号"] == "-5"


def test_salvage_records_from_truncated_json() -> None:
    raw = """{
  "records": [
    {"检测位置":"B2","行号":"通用","管号":"-2","壁厚":6.9},
    {"检测位置":"B2","行号":"通用","管号":"-4","壁厚":6.5},
    {"检测位置":"B2","行号":"通用","管号":"-6","壁厚":
"""
    out = _salvage_records_from_truncated_json(raw)
    assert len(out) == 2
    assert out[1]["管号"] == "-4"


def test_summarize_chunk_has_ranges_and_preview() -> None:
    chunk = "\n".join(
        [
            "[处理单元 heading_path=（一）炉膛水冷壁检查情况]",
            "这是说明文本",
            "[DOCX_V2_TABLE idx=3 rows=2 cols=2]",
            "r0: c0='x' | c1='1'",
            "r1: c0='y' | c1='2'",
        ]
    )
    meta = _summarize_chunk(chunk)
    assert meta["heading_path"] == "（一）炉膛水冷壁检查情况"
    assert meta["text_lines"] == 1
    assert meta["table_blocks"] == 1
    assert meta["table_idx_range"] == "3-3"
    assert meta["row_idx_range"] == "0-1"
    assert meta["chunk_sha1"] != "-"
