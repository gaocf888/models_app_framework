"""站点短码 → 完整场地名提升（词表 id 与 project_name）。"""

from __future__ import annotations


def promote_station_ids(
    station_id: str | None,
    station_name: str | None,
) -> tuple[str | None, str | None]:
    """词表 id 可能是短码 F2，场地键提升为完整 project_name：F2(望京)。"""
    sid = (station_id or None) and str(station_id).strip() or None
    sname = (station_name or None) and str(station_name).strip() or None
    if sname and (not sid or (str(sid) != str(sname) and str(sid) in str(sname))):
        sid = sname
    return sid, sname
