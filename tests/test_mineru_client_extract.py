"""MinerU HTTP 响应解析与官方 JSON 结构对齐（md_content）。"""

from __future__ import annotations

from pathlib import Path

from app.rag.mineru_response_parse import extract_markdown_from_json, read_markdown_from_disk


def test_extract_md_content_nested_results() -> None:
    payload = {
        "backend": "pipeline",
        "version": "9.9.9",
        "results": {"mydoc": {"md_content": "# Hello\n", "middle_json": None}},
    }
    assert extract_markdown_from_json(payload) == "# Hello"


def test_read_markdown_from_disk_rglob(tmp_path: Path) -> None:
    tid = "550e8400-e29b-41d4-a716-446655440000"
    md_path = tmp_path / "mineru-output" / tid / "auto" / "mydoc.md"
    md_path.parent.mkdir(parents=True)
    md_path.write_text("## From disk\n", encoding="utf-8")
    got = read_markdown_from_disk(tmp_path, tid, output_subdir="mineru-output")
    assert got == "## From disk"
