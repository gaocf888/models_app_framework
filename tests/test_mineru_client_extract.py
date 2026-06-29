"""MinerU HTTP 响应解析与官方 JSON 结构对齐（md_content）。"""

from __future__ import annotations

from pathlib import Path

from app.rag.mineru_response_parse import (
    discover_image_base_dir,
    extract_markdown_from_json,
    materialize_zip_output,
    read_markdown_from_disk,
)


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


def test_materialize_zip_output_extracts_md_and_images(tmp_path: Path) -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("auto/doc.md", "# Title\n\n![](images/a.png)\n")
        zf.writestr("auto/images/a.png", b"\x89PNG\r\n\x1a\n")
    md, img_base = materialize_zip_output(buf.getvalue(), tmp_path / "task1")
    assert md and "Title" in md
    assert img_base is not None
    assert discover_image_base_dir(tmp_path / "task1") is not None
    assert (img_base / "a.png").is_file()
