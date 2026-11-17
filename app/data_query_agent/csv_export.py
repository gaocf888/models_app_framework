"""把 columns + list 编成 GB18030 CSV，供浏览/解析导出下载（中文 Excel 直接打开）。"""

from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi.responses import Response

_UNSAFE_NAME = re.compile(r'[\\/:*?"<>|\r\n]+')
_SKIP_COL_PREFIXES = ("hud_",)
# 中文 Windows Excel 打开 CSV 默认按 ANSI/GBK，不用 UTF-8（即使带 BOM 也可能被网关剥掉）。
_CSV_ENCODING = "gb18030"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        try:
            return format(value, "f")
        except Exception:  # noqa: BLE001
            return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _flatten_export_columns(columns: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """点站两级表头展开为叶子列；CSV 表头用「站点-指标」。"""
    out: list[dict[str, Any]] = []
    for col in columns or []:
        if not isinstance(col, dict):
            continue
        children = col.get("children")
        if isinstance(children, list) and children:
            parent = str(col.get("title") or col.get("key") or "").strip()
            for child in children:
                if not isinstance(child, dict):
                    continue
                key = str(child.get("key") or "").strip()
                if not key or key.startswith(_SKIP_COL_PREFIXES):
                    continue
                child_title = str(child.get("title") or key).strip()
                title = f"{parent}-{child_title}" if parent else child_title
                out.append({"key": key, "title": title})
            continue
        key = str(col.get("key") or "").strip()
        if not key or key.startswith(_SKIP_COL_PREFIXES):
            continue
        out.append({"key": key, "title": str(col.get("title") or key)})
    return out


def table_to_csv_bytes(
    columns: list[dict[str, Any]] | None,
    rows: list[dict[str, Any]] | None,
) -> bytes:
    """表头用 columns[].title，格取 list[i][columns[j].key]；GB18030 方便中文 Excel。"""
    cols = _flatten_export_columns(columns)
    keys = [str(c.get("key") or "") for c in cols]
    titles = [str(c.get("title") or c.get("key") or "") for c in cols]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(titles)
    for row in rows or []:
        item = row if isinstance(row, dict) else {}
        writer.writerow([_cell(item.get(k) if k else None) for k in keys])
    return buf.getvalue().encode(_CSV_ENCODING, errors="replace")


def csv_filename(prefix: str, *parts: Any, suffix: str = ".csv") -> str:
    bits = [str(prefix or "export").strip() or "export"]
    for raw in parts:
        text = str(raw or "").strip()
        if not text:
            continue
        bits.append(_UNSAFE_NAME.sub("_", text).strip(" ._") or "x")
    name = "-".join(bits) + suffix
    return name[:180]


def csv_attachment_response(content: bytes, filename: str) -> Response:
    safe = csv_filename("file") if not filename else filename
    ascii_name = "export.csv" if not all(ord(ch) < 128 for ch in safe) else safe
    quoted = quote(safe)
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"
    return Response(
        content=content,
        # 按附件字节下发，避免 text/csv; charset=utf-8 被网关当字符串转码后丢掉编码信息。
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": disposition,
            "Content-Type": "application/octet-stream",
        },
    )
