"""
MinerU HTTP 响应体解析（无 httpx 依赖，便于单测与复用）。
与 opendatalab/MinerU mineru/cli/fast_api.py 中 build_result_dict 字段对齐。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# 官方 JSON：results[<stem>]["md_content"]
_MD_JSON_KEYS = frozenset(
    {"md_content", "markdown", "md", "result_md", "text", "content", "result", "body"}
)


def extract_markdown_from_json(obj: Any) -> str | None:
    """从 JSON 对象中提取 Markdown 字符串（仅白名单键，避免误用 detail/error）。"""
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _MD_JSON_KEYS and isinstance(v, str) and v.strip():
                return v.strip()
        for k, v in obj.items():
            if k in _MD_JSON_KEYS and isinstance(v, (dict, list)):
                found = extract_markdown_from_json(v)
                if found:
                    return found
        for _k, v in obj.items():
            if isinstance(v, (dict, list)):
                found = extract_markdown_from_json(v)
                if found:
                    return found
    if isinstance(obj, list):
        for item in obj:
            found = extract_markdown_from_json(item)
            if found:
                return found
    return None


def markdown_from_zip_bytes(data: bytes) -> str | None:
    if not data.startswith(b"PK\x03\x04"):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".md")]
            names.sort()
            for name in names:
                raw = zf.read(name)
                try:
                    return raw.decode("utf-8")
                except UnicodeDecodeError:
                    return raw.decode("utf-8", errors="replace")
    except zipfile.BadZipFile:
        return None
    return None


def read_markdown_from_disk(io_base: Path, task_id: str, *, output_subdir: str) -> str | None:
    """
    当 HTTP 体未带 md 时，从与 MinerU 共享卷读取。
    须将 mineru-api 的 MINERU_API_OUTPUT_ROOT 设为容器内可写子目录（如 /io/mineru-output），
    与 output_subdir（宿主侧相对 io 挂载根）一致。
    """
    tid = task_id.strip()
    if not tid:
        return None
    root = io_base / output_subdir / tid
    if not root.exists():
        return None
    md_files = sorted(root.rglob("*.md"))
    if not md_files:
        return None
    parts: list[str] = []
    for p in md_files:
        try:
            parts.append(p.read_text(encoding="utf-8"))
        except OSError as e:
            logger.warning("mineru read md file failed: %s %s", p, e)
    return "\n\n".join(parts).strip() if parts else None
