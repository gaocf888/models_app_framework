"""无 geopandas 时读取本地 shapefile（WGS84 多边形/折线 + DBF）。"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any


def _decode_dbf_text(raw: bytes) -> str:
    text = raw.replace(b"\x00", b" ").strip()
    if not text:
        return ""
    for enc in ("gbk", "gb2312", "utf-8", "latin1"):
        try:
            return text.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return text.decode("latin1", errors="replace").strip()


def read_dbf_rows(path: Path) -> list[dict[str, Any]]:
    data = path.read_bytes()
    if len(data) < 32:
        return []
    nrec = struct.unpack_from("<I", data, 4)[0]
    hlen = struct.unpack_from("<H", data, 8)[0]
    rlen = struct.unpack_from("<H", data, 10)[0]
    fields: list[tuple[str, str, int]] = []
    pos = 32
    while pos + 32 <= len(data) and data[pos] != 0x0D:
        name = data[pos : pos + 11].split(b"\x00")[0].decode("ascii", errors="replace").strip()
        typ = chr(data[pos + 11])
        flen = data[pos + 16]
        fields.append((name, typ, flen))
        pos += 32
    rows: list[dict[str, Any]] = []
    for i in range(nrec):
        rec = data[hlen + i * rlen : hlen + (i + 1) * rlen]
        if len(rec) < rlen:
            break
        offset = 1
        item: dict[str, Any] = {}
        for name, typ, flen in fields:
            raw = rec[offset : offset + flen]
            offset += flen
            val = _decode_dbf_text(raw)
            if typ in {"N", "F"} and val:
                try:
                    item[name] = float(val) if "." in val else int(val)
                    continue
                except ValueError:
                    pass
            item[name] = val
        rows.append(item)
    return rows


def _parse_parts_points(chunk: bytes) -> list[list[tuple[float, float]]]:
    if len(chunk) < 44:
        return []
    num_parts, num_points = struct.unpack_from("<2i", chunk, 36)
    if num_parts < 0 or num_points < 0:
        return []
    parts_off = 44
    pts_off = 44 + num_parts * 4
    if pts_off + num_points * 16 > len(chunk):
        return []
    parts = struct.unpack_from(f"<{num_parts}i", chunk, parts_off)
    pts: list[tuple[float, float]] = []
    for i in range(num_points):
        x, y = struct.unpack_from("<2d", chunk, pts_off + i * 16)
        pts.append((x, y))
    rings: list[list[tuple[float, float]]] = []
    for i, start in enumerate(parts):
        end = parts[i + 1] if i + 1 < num_parts else num_points
        ring = pts[start:end]
        if len(ring) >= 2:
            rings.append(ring)
    return rings


def iter_shp_rings(path: Path, *, want_types: tuple[int, ...] = (3, 5)) -> list[tuple[int, list[list[tuple[float, float]]]]]:
    data = path.read_bytes()
    if len(data) < 100:
        return []
    offset = 100
    out: list[tuple[int, list[list[tuple[float, float]]]]] = []
    while offset + 8 <= len(data):
        _recno, clen = struct.unpack_from(">2i", data, offset)
        offset += 8
        nbytes = clen * 2
        chunk = data[offset : offset + nbytes]
        offset += nbytes
        if len(chunk) < 4:
            break
        stype = struct.unpack_from("<i", chunk, 0)[0]
        if stype not in want_types:
            continue
        rings = _parse_parts_points(chunk)
        if rings:
            out.append((stype, rings))
    return out


def ring_bbox(ring: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not ring:
        return None
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def bbox_intersects(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def first_shp(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    named = list(directory.glob("*.shp"))
    return named[0] if named else None


def iter_shp_features(
    path: Path,
    *,
    want_types: tuple[int, ...] = (3, 5),
) -> list[tuple[int, list[list[tuple[float, float]]], dict[str, Any]]]:
    recs = iter_shp_rings(path, want_types=want_types)
    dbf = path.with_suffix(".dbf")
    attrs = read_dbf_rows(dbf) if dbf.is_file() else []
    out: list[tuple[int, list[list[tuple[float, float]]], dict[str, Any]]] = []
    for i, (stype, rings) in enumerate(recs):
        attr = attrs[i] if i < len(attrs) else {}
        out.append((stype, rings, attr if isinstance(attr, dict) else {}))
    return out


def attr_text(attr: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in attr and attr[key] not in (None, ""):
            return str(attr[key]).strip()
        low = key.lower()
        for ak, av in attr.items():
            if str(ak).lower() == low and av not in (None, ""):
                return str(av).strip()
    return ""


def polyline_length_m(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        lat = (y0 + y1) / 2.0
        dx = (x1 - x0) * 111320.0 * math.cos(math.radians(lat))
        dy = (y1 - y0) * 111320.0
        total += math.hypot(dx, dy)
    return total


def ring_area_m2(ring: list[tuple[float, float]]) -> float:
    if len(ring) < 3:
        return 0.0
    lat = sum(p[1] for p in ring) / len(ring)
    scale_x = 111320.0 * math.cos(math.radians(lat))
    xs = [(p[0] - ring[0][0]) * scale_x for p in ring]
    ys = [(p[1] - ring[0][1]) * 111320.0 for p in ring]
    acc = 0.0
    for i in range(len(xs) - 1):
        acc += xs[i] * ys[i + 1] - xs[i + 1] * ys[i]
    return abs(acc) * 0.5
