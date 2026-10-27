"""把 columns + list 编成 UTF-8 BOM CSV，供浏览/解析导出下载。"""

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


def table_to_csv_bytes(
    columns: list[dict[str, Any]] | None,
    rows: list[dict[str, Any]] | None,
) -> bytes:
    """表头用 columns[].title，格取 list[i][columns[j].key]；UTF-8 BOM 方便 Excel。"""
    cols = [
        c
        for c in (columns or [])
        if str(c.get("key") or "") and not str(c.get("key") or "").startswith(_SKIP_COL_PREFIXES)
    ]
    keys = [str(c.get("key") or "") for c in cols]
    titles = [str(c.get("title") or c.get("key") or "") for c in cols]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(titles)
    for row in rows or []:
        item = row if isinstance(row, dict) else {}
        writer.writerow([_cell(item.get(k) if k else None) for k in keys])
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


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
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )
