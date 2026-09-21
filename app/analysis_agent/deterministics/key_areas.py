"""重点监测区表 4-1：YAML 13 站 JOIN 层位 0 与压缩层对。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.analysis_agent.deterministics.coverage import canonical_project, normalize_site_key
from app.core.logging import get_logger

logger = get_logger(__name__)

_YAML = (
    Path(__file__).resolve().parents[3]
    / "configs"
    / "nl2sql_business"
    / "subsidence"
    / "semantic"
    / "dimensions"
    / "key_monitoring_areas.yaml"
)

_COMPRESS_COLS = {
    (0, 1): "c01",
    (1, 2): "c12",
    (2, 3): "c23",
    (3, 4): "c34",
}


@lru_cache(maxsize=1)
def load_key_area_stations() -> tuple[dict[str, Any], ...]:
    raw = yaml.safe_load(_YAML.read_text(encoding="utf-8")) if _YAML.is_file() else {}
    entries = (raw or {}).get("entries") or []
    out: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for area in entries:
        if not isinstance(area, dict):
            continue
        area_name = str(area.get("area_name") or "").strip()
        stations = area.get("stations") or []
        station_count = int(area.get("station_count") or len(stations) or 0)
        for st in stations:
            if not isinstance(st, dict):
                continue
            raw_name = str(st.get("project_name") or "").strip()
            canon = canonical_project(raw_name, "fcb")
            if not canon:
                unmatched.append(raw_name or str(st.get("station_code") or ""))
            out.append(
                {
                    "area_name": area_name,
                    "station_count": station_count,
                    "station_code": str(st.get("station_code") or "").strip(),
                    "project_name": canon or normalize_site_key(raw_name),
                    "layer0_station_name": str(st.get("layer0_station_name") or "").strip(),
                    "location_raw": str(st.get("location_raw") or "").strip(),
                }
            )
    if unmatched:
        logger.warning("key_monitoring_areas coverage mismatch sample=%s", unmatched[:8])
    return tuple(out)


def join_key_areas(gathered: dict[str, list[dict[str, Any]]], **_: Any) -> list[dict[str, Any]]:
    """
    每个配置站一行。缺 compress 列保持 None（F11 三四层、F9/F10 第四层等）。
    季报表 4-1 列：地区、监测站总数、监测站编号、总沉降、四个压缩层组。
    """
    layer0 = {
        str(r.get("project_name") or ""): r
        for r in (gathered.get("q_layer0") or [])
        if isinstance(r, dict) and r.get("project_name")
    }
    compress_by: dict[str, dict[str, Any]] = {}
    for row in gathered.get("q_compress") or []:
        if not isinstance(row, dict):
            continue
        project = str(row.get("project_name") or "")
        pair = (int(row.get("layer_from") or -1), int(row.get("layer_to") or -1))
        col = _COMPRESS_COLS.get(pair)
        if not project or not col:
            continue
        compress_by.setdefault(project, {})[col] = row.get("compress_mm")

    out: list[dict[str, Any]] = []
    for st in load_key_area_stations():
        project = st["project_name"]
        l0 = layer0.get(project) or {}
        c = compress_by.get(project) or {}
        out.append(
            {
                "area_name": st["area_name"],
                "station_count": st["station_count"],
                "station_code": st["station_code"],
                "project_name": project,
                "location_raw": st["location_raw"],
                "total_delta_mm": l0.get("delta_mm"),
                "c01": c.get("c01"),
                "c12": c.get("c12"),
                "c23": c.get("c23"),
                "c34": c.get("c34"),
            }
        )
    return out
