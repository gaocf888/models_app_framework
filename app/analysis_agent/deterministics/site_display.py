"""层位 0 站点对外展示：地址 + 站码（季报附表1 口径）。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.analysis_agent.deterministics.coverage import canonical_project, site_code
from app.core.logging import get_logger

logger = get_logger(__name__)

_YAML = (
    Path(__file__).resolve().parents[3]
    / "configs"
    / "nl2sql_business"
    / "subsidence"
    / "semantic"
    / "dimensions"
    / "layer0_site_display.yaml"
)


@dataclass(frozen=True)
class SiteDisplay:
    project_name: str
    station_code: str
    area: str
    location: str
    township: str
    annex_label: str


@lru_cache(maxsize=1)
def load_site_displays() -> tuple[SiteDisplay, ...]:
    raw = yaml.safe_load(_YAML.read_text(encoding="utf-8")) if _YAML.is_file() else {}
    entries = (raw or {}).get("entries") or []
    out: list[SiteDisplay] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        project = str(item.get("project_name") or "").strip()
        code = str(item.get("station_code") or site_code(project) or "").strip()
        location = str(item.get("location") or "").strip()
        annex = str(item.get("annex_label") or "").strip() or f"{location}{code}"
        out.append(
            SiteDisplay(
                project_name=project,
                station_code=code,
                area=str(item.get("area") or "").strip(),
                location=location,
                township=str(item.get("township") or "").strip(),
                annex_label=annex,
            )
        )
    return tuple(out)


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, SiteDisplay], dict[str, SiteDisplay]]:
    by_project: dict[str, SiteDisplay] = {}
    by_code: dict[str, SiteDisplay] = {}
    for item in load_site_displays():
        keys = {item.project_name}
        canon = canonical_project(item.project_name, "fcb")
        if canon:
            keys.add(canon)
        for key in keys:
            if key:
                by_project[key] = item
        if item.station_code:
            by_code[item.station_code.upper()] = item
    return by_project, by_code


def lookup_site_display(project_name: str) -> SiteDisplay | None:
    raw = (project_name or "").strip()
    if not raw:
        return None
    by_project, by_code = _index()
    if raw in by_project:
        return by_project[raw]
    canon = canonical_project(raw, "fcb")
    if canon and canon in by_project:
        return by_project[canon]
    code = site_code(raw)
    if code:
        return by_code.get(code.upper())
    return None


def attach_site_display(project_name: str, area: str | None = None) -> dict[str, str]:
    """给层位0/压缩层行补对外字段。"""
    info = lookup_site_display(project_name)
    if info is None:
        code = site_code(project_name)
        logger.warning("layer0_site_display miss project=%s", project_name)
        return {
            "station_code": code,
            "location": "",
            "township": "",
            "annex_label": code or (project_name or "").strip() or "待补充",
            "display_clause": f"{code}监测站" if code else "未命名监测站",
        }
    area_name = (area or "").strip() or info.area
    return {
        "station_code": info.station_code,
        "location": info.location,
        "township": info.township,
        "annex_label": info.annex_label,
        "display_clause": f"位于{area_name}{info.location}的{info.station_code}监测站",
    }


def township_breakdown(rows: list[dict[str, Any]]) -> str:
    """「其中：望京街道1座、金盏乡2座」。"""
    counts: dict[str, int] = {}
    for row in rows:
        name = str(row.get("township") or "").strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return ""
    parts = [f"{name}{n}座" for name, n in counts.items()]
    return "其中：" + "、".join(parts)
